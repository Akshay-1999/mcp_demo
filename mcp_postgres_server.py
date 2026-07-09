import os
import asyncio
import asyncpg
import logging
import sys
import re
from typing import Any, Optional
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import pandas as pd

# Configure logging to stderr
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger("mcp_postgres_server")

# Load environment variables
load_dotenv()

# Initialize FastMCP server
mcp = FastMCP("PostgreSQL-Secure")

# Global state for last query results
LAST_QUERY_RESULTS = None

def get_db_url():
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    host = os.getenv("DB_HOST")
    port = os.getenv("DB_PORT")
    dbname = os.getenv("DB_NAME")
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"

@mcp.tool()
async def list_schemas() -> str:
    """
    List all user-defined schemas in the database.
    Use this to understand the organization of the database.
    """
    logger.info("Executing list_schemas tool")
    conn = None
    try:
        conn = await asyncpg.connect(get_db_url())
        rows = await conn.fetch(
            """
            SELECT nspname as schema_name
            FROM pg_namespace
            WHERE nspname NOT LIKE 'pg_%'
            AND nspname <> 'information_schema'
            ORDER BY nspname
            """
        )
        if not rows:
            return "No user-defined schemas found."
        return "Available schemas:\n" + "\n".join(f"- {r['schema_name']}" for r in rows)
    except Exception as e:
        logger.error(f"Error in list_schemas: {e}")
        return f"Error listing schemas: {str(e)}"
    finally:
        if conn:
            await conn.close()

@mcp.tool()
async def list_tables(schema: Optional[str] = None) -> str:
    """
    List tables in the database.
    If schema is provided, lists tables in that schema.
    Otherwise, lists tables in all user-defined schemas.
    """
    logger.info(f"Executing list_tables tool (schema: {schema})")
    conn = None
    try:
        conn = await asyncpg.connect(get_db_url())
        if schema:
            query = """
                SELECT n.nspname as table_schema, c.relname as table_name,
                       CASE WHEN c.relkind = 'v' THEN 'VIEW' ELSE 'BASE TABLE' END as table_type
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = $1
                AND c.relkind IN ('r', 'v')
                ORDER BY c.relname
            """
            rows = await conn.fetch(query, schema)
        else:
            query = """
                SELECT n.nspname as table_schema, c.relname as table_name,
                       CASE WHEN c.relkind = 'v' THEN 'VIEW' ELSE 'BASE TABLE' END as table_type
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname NOT LIKE 'pg_%'
                AND n.nspname <> 'information_schema'
                AND c.relkind IN ('r', 'v')
                ORDER BY n.nspname, c.relname
            """
            rows = await conn.fetch(query)

        if not rows:
            return f"No tables or views found{' in schema ' + schema if schema else ''}."
        
        tables = [f"{row['table_schema']}.{row['table_name']} ({row['table_type']})" for row in rows]
        return f"Database objects{' in schema ' + schema if schema else ''}:\n" + "\n".join(f"- {t}" for t in tables)
    except Exception as e:
        logger.error(f"Error in list_tables: {e}")
        return f"Error listing tables: {str(e)}"
    finally:
        if conn:
            await conn.close()

@mcp.tool()
async def describe_table(table_name: str, schema: Optional[str] = None) -> str:
    """
    Describe the schema of a specific table.
    - table_name: can be 'table' or 'schema.table'
    - schema: optional, if not provided in table_name
    """
    logger.info(f"Executing describe_table tool for: {table_name} (schema: {schema})")
    conn = None
    try:
        target_schema = schema
        target_table = table_name

        if '.' in table_name:
            target_schema, target_table = table_name.split('.', 1)
        
        conn = await asyncpg.connect(get_db_url())
        
        # If schema still not identified, try to find it
        if not target_schema:
            find_query = """
                SELECT table_schema 
                FROM information_schema.tables 
                WHERE table_name = $1 
                AND table_schema NOT IN ('pg_catalog', 'information_schema')
            """
            schema_rows = await conn.fetch(find_query, target_table)
            if len(schema_rows) == 1:
                target_schema = schema_rows[0]['table_schema']
            elif len(schema_rows) > 1:
                schemas = ", ".join([r['table_schema'] for r in schema_rows])
                return f"Table '{target_table}' exists in multiple schemas: {schemas}. Please specify the schema."
            else:
                target_schema = 'public' # Fallback

        rows = await conn.fetch(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = $1
            AND table_schema = $2
            ORDER BY ordinal_position
            """,
            target_table, target_schema
        )
        if not rows:
            return f"Table '{target_table}' (schema '{target_schema}') not found or has no accessible columns."
        
        description = f"Schema for table '{target_table}':\n"
        for row in rows:
            nullable = "NULL" if row['is_nullable'] == 'YES' else "NOT NULL"
            description += f"- {row['column_name']}: {row['data_type']} ({nullable})\n"
        return description
    except Exception as e:
        logger.error(f"Error in describe_table: {e}")
        return f"Error describing table: {str(e)}"
    finally:
        if conn:
            await conn.close()

@mcp.tool()
async def execute_query(sql: str, user_id: Optional[str] = None) -> str:
    """
    Execute a SELECT SQL query with Row Level Security (RLS) support.
    
    Args:
        sql: The SELECT query to run.
        user_id: Optional ID to set for Row Level Security policies.
    """
    # Safety Check: Only SELECT allowed
    if not re.match(r"^\s*SELECT", sql, re.IGNORECASE):
        return "Error: Only SELECT queries are permitted for safety reasons."
    
    # Keyword blocklist
    forbidden = ["DELETE", "UPDATE", "INSERT", "DROP", "TRUNCATE", "ALTER", "GRANT", "REVOKE"]
    if any(re.search(rf"\b{k}\b", sql, re.IGNORECASE) for k in forbidden):
        return "Error: Query contains forbidden destructive keywords."

    logger.info(f"Executing execute_query tool with user_id: {user_id}")
    conn = None
    try:
        conn = await asyncpg.connect(get_db_url())
        async with conn.transaction():
            if user_id:
                sanitized_id = re.sub(r"[^a-zA-Z0-9_\-]", "", str(user_id))
                await conn.execute(f"SET LOCAL app.current_user_id = '{sanitized_id}'")
                logger.info(f"Set LOCAL app.current_user_id to: {sanitized_id}")
            
            global LAST_QUERY_RESULTS
            rows = await conn.fetch(sql)
            logger.info(f"Query returned {len(rows)} rows")
            if not rows:
                LAST_QUERY_RESULTS = []
                return "Query executed successfully, but returned no rows."
            
            LAST_QUERY_RESULTS = [dict(row) for row in rows]
            
            headers = list(rows[0].keys())
            header_row = " | ".join(headers)
            separator = "-" * len(header_row)
            
            output = [header_row, separator]
            for row in rows:
                output.append(" | ".join(str(val) for val in row.values()))
            
            return "\n".join(output)
    except Exception as e:
        logger.error(f"Error in execute_query: {e}")
        return f"Error executing query: {str(e)}"
    finally:
        if conn:
            await conn.close()

@mcp.tool()
async def export_last_result_to_csv(filename: str = "export.csv") -> str:
    """
    Export the results of the most recently executed SQL query to a CSV file.
    Use this when the user asks to export the data you just retrieved.
    
    Args:
        filename: The output filename (e.g., 'report.csv'). Will be saved in the current directory.
    """
    global LAST_QUERY_RESULTS
    
    logger.info("Executing export_last_result_to_csv tool")
    if LAST_QUERY_RESULTS is None:
        return "Error: No query has been executed yet. Run a query first."
    if not LAST_QUERY_RESULTS:
        return "Error: The last executed query returned no data."
        
    try:
        df = pd.DataFrame(LAST_QUERY_RESULTS)
        
        if not filename.endswith('.csv'):
            filename += '.csv'
            
        filepath = os.path.abspath(filename)
        df.to_csv(filepath, index=False)
        
        return f"Successfully exported {len(LAST_QUERY_RESULTS)} rows to CSV file at: {filepath}"
    except Exception as e:
        logger.error(f"Error in export_last_result_to_csv: {e}")
        return f"Error exporting to CSV: {str(e)}"

if __name__ == "__main__":
    logger.info("PostgreSQL Secure MCP server starting...")
    mcp.run()
