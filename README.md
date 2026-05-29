# PostgreSQL MCP Server

This is a Model Context Protocol (MCP) server that provides an interface to a PostgreSQL database. It allows an AI assistant (like Claude Desktop) to:
1. List available tables.
2. Inspect table schemas.
3. Execute SQL queries to answer natural language questions.

## Setup

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure environment variables**:
   Copy `.env.example` to `.env` and fill in your database credentials:
   ```bash
   cp .env.example .env
   ```

3. **Run the server**:
   ```bash
   python mcp_postgres_server.py
   ```

## Using with Claude Desktop

To use this with Claude Desktop, add the following to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "postgres": {
      "command": "python",
      "args": ["/path/to/your/mcp_postgres_server.py"],
      "env": {
        "DB_HOST": "your_host",
        "DB_PORT": "5432",
        "DB_NAME": "your_db",
        "DB_USER": "your_user",
        "DB_PASSWORD": "your_password"
      }
    }
  }
}
```

## How it works

When you ask a natural language question like "Show me the top 5 users by activity", the AI will:
1. Call `list_tables()` to see which tables exist.
2. Call `describe_table("users")` (or others) to see the columns.
3. Generate a SQL query like `SELECT name, activity_count FROM users ORDER BY activity_count DESC LIMIT 5`.
4. Call `execute_query(sql)` to get the data.
5. Present the answer to you.
