import asyncio
import os
import sys
from langchain_mcp_adapters.client import MultiServerMCPClient  
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
load_dotenv()

async def main():
    # Keep the MCP server running for the entire session
    async with MultiServerMCPClient(
        {
            "postgres": {
                "transport": "stdio",  # Local subprocess communication
                "command": sys.executable,
                "args": ["./mcp_postgres_server.py"],
            }
        }
    ) as client:

        tools = client.get_tools()
        print("Available tools:", [t.name for t in tools])
        llm = ChatOpenAI(model="gpt-4o-mini", api_key=os.getenv("OPENAI_API_KEY"))
        
        system_message = (
            "You are an expert PostgreSQL Data Analyst. Your goal is to provide accurate, high-quality data analysis.\n\n"
            "### CRITICAL RULES:\n"
            "1. **DO NOT PLAN, EXECUTE**: Do not just describe what you will do. **Actually call the tools** (execute_query) to get the data. Your response is not complete until you show the data.\n"
            "2. **Explore First**: Always use `list_tables` and `describe_table` to verify schema, column names, and data types before writing SQL.\n"
            "3. **Schema Qualification**: Always prefix tables with their schema (e.g., `core.users`).\n"
            "4. **Formatting**: Present results in Markdown tables. If a query returns no data, explicitly state 'No results found in [table]'.\n"
            "5. **Exporting to CSV**: If the user asks to export the data or the query to CSV, simply call the `export_last_result_to_csv` tool without asking for the query. Assume the server remembers the last query.\n\n"
            "### Accuracy Guidelines:\n"
            "- Verify data types: check if scores are integers or decimals.\n"
            "- Join verification: describe both tables to confirm join keys."
        )

        prompt = ChatPromptTemplate.from_messages([
            ("system", system_message),
            MessagesPlaceholder(variable_name="chat_history"),
            ("user", "{input}"),
            ("placeholder", "{agent_scratchpad}"),
        ])
        
        agent = create_tool_calling_agent(
            llm=llm,
            tools=tools,
            prompt=prompt
        )
        agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True, max_iterations=15)
        
        chat_history = []
        
        while True:
            try:
                user_input = input("Ask (or type 'quit' to exit): ")
                if user_input.lower() in ['quit', 'exit', 'q']:
                    break
                    
                response = await agent_executor.ainvoke({
                    "input": user_input,
                    "chat_history": chat_history
                })
                
                print(response['output'])
                
                # Update memory
                chat_history.append(HumanMessage(content=user_input))
                chat_history.append(AIMessage(content=response['output']))
                print(f"\n\n-----------------------------------------------------------")
                
            except (KeyboardInterrupt, EOFError):
                break

if __name__ == "__main__":
    asyncio.run(main())

