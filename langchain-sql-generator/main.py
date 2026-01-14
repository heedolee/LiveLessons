"""
FastAPI application with LangChain 1.0.0 for Natural Language to SQL conversion
"""
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import os
from pathlib import Path
import logging

from langchain_community.utilities import SQLDatabase
from langchain_openai import ChatOpenAI
from langchain.chains import create_sql_query_chain
from langchain_community.tools.sql_database.tool import QuerySQLDataBaseTool
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

# Logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI app initialization
app = FastAPI(
    title="LangChain SQL Generator API",
    description="Natural Language to SQL conversion using LangChain and MariaDB",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models
class QueryRequest(BaseModel):
    question: str

class QueryResponse(BaseModel):
    question: str
    sql_query: str
    results: List[Dict[str, Any]]
    row_count: int

class SchemaResponse(BaseModel):
    schema: str
    table_info: str

class HealthResponse(BaseModel):
    status: str
    database_connected: bool
    llm_configured: bool

# Global variables
db_instance = None
llm_instance = None
schema_description = ""

# Configuration
class Config:
    MARIADB_HOST = os.getenv("MARIADB_HOST", "localhost")
    MARIADB_PORT = os.getenv("MARIADB_PORT", "3306")
    MARIADB_USER = os.getenv("MARIADB_USER", "root")
    MARIADB_PASSWORD = os.getenv("MARIADB_PASSWORD", "password")
    MARIADB_DATABASE = os.getenv("MARIADB_DATABASE", "testdb")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4")
    LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0"))
    SCHEMA_FILE = os.getenv("SCHEMA_FILE", "sample.txt")

def get_database_uri() -> str:
    """Generate database URI for MariaDB connection"""
    return (
        f"mysql+pymysql://{Config.MARIADB_USER}:{Config.MARIADB_PASSWORD}"
        f"@{Config.MARIADB_HOST}:{Config.MARIADB_PORT}/{Config.MARIADB_DATABASE}"
    )

def load_schema_description() -> str:
    """Load database schema description from sample.txt"""
    schema_file_path = Path(__file__).parent / Config.SCHEMA_FILE

    try:
        with open(schema_file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        logger.info(f"Schema description loaded from {schema_file_path}")
        return content
    except FileNotFoundError:
        logger.warning(f"Schema file not found: {schema_file_path}")
        return ""
    except Exception as e:
        logger.error(f"Error loading schema description: {e}")
        return ""

def initialize_database() -> SQLDatabase:
    """Initialize database connection and load schema"""
    try:
        db_uri = get_database_uri()
        db = SQLDatabase.from_uri(
            db_uri,
            sample_rows_in_table_info=3,
            include_tables=None,  # Include all tables
        )
        logger.info(f"Database connected successfully to {Config.MARIADB_DATABASE}")
        return db
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise

def initialize_llm() -> ChatOpenAI:
    """Initialize LLM instance"""
    if not Config.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY environment variable is not set")

    llm = ChatOpenAI(
        model=Config.LLM_MODEL,
        temperature=Config.LLM_TEMPERATURE,
        api_key=Config.OPENAI_API_KEY
    )
    logger.info(f"LLM initialized with model: {Config.LLM_MODEL}")
    return llm

@app.on_event("startup")
async def startup_event():
    """Initialize resources on startup"""
    global db_instance, llm_instance, schema_description

    try:
        # Load schema description
        schema_description = load_schema_description()

        # Initialize database
        db_instance = initialize_database()

        # Initialize LLM
        llm_instance = initialize_llm()

        logger.info("Application startup completed successfully")
    except Exception as e:
        logger.error(f"Startup failed: {e}")
        # Don't raise - allow app to start but endpoints will return errors

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup resources on shutdown"""
    logger.info("Application shutdown")

def get_db():
    """Dependency to get database instance"""
    if db_instance is None:
        raise HTTPException(status_code=503, detail="Database not initialized")
    return db_instance

def get_llm():
    """Dependency to get LLM instance"""
    if llm_instance is None:
        raise HTTPException(status_code=503, detail="LLM not initialized")
    return llm_instance

@app.get("/", response_model=Dict[str, str])
async def root():
    """Root endpoint"""
    return {
        "message": "LangChain SQL Generator API",
        "version": "1.0.0",
        "docs": "/docs"
    }

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    return HealthResponse(
        status="healthy",
        database_connected=db_instance is not None,
        llm_configured=llm_instance is not None
    )

@app.get("/schema", response_model=SchemaResponse)
async def get_schema(db: SQLDatabase = Depends(get_db)):
    """Get database schema information"""
    try:
        table_info = db.get_table_info()
        return SchemaResponse(
            schema=schema_description,
            table_info=table_info
        )
    except Exception as e:
        logger.error(f"Error getting schema: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting schema: {str(e)}")

@app.post("/query", response_model=QueryResponse)
async def query_database(
    request: QueryRequest,
    db: SQLDatabase = Depends(get_db),
    llm: ChatOpenAI = Depends(get_llm)
):
    """
    Convert natural language question to SQL and execute query

    Args:
        request: QueryRequest containing the natural language question

    Returns:
        QueryResponse with SQL query and results
    """
    try:
        logger.info(f"Processing query: {request.question}")

        # Create SQL query chain with schema description
        chain = create_sql_query_chain(llm, db)

        # Add schema description to the prompt if available
        enhanced_question = request.question
        if schema_description:
            enhanced_question = f"""
Schema Description:
{schema_description}

User Question: {request.question}

Please generate a SQL query based on the schema description and user question above.
"""

        # Generate SQL query from natural language
        sql_query = chain.invoke({"question": enhanced_question})

        # Clean up SQL query (remove markdown code blocks if present)
        sql_query = sql_query.strip()
        if sql_query.startswith("```sql"):
            sql_query = sql_query[6:]
        if sql_query.startswith("```"):
            sql_query = sql_query[3:]
        if sql_query.endswith("```"):
            sql_query = sql_query[:-3]
        sql_query = sql_query.strip()

        logger.info(f"Generated SQL: {sql_query}")

        # Execute SQL query
        execute_query = QuerySQLDataBaseTool(db=db)
        result = execute_query.invoke(sql_query)

        # Parse results
        results = []
        if result:
            # Try to parse the result as a list of tuples
            try:
                # The result might be a string representation
                if isinstance(result, str):
                    # Execute query directly to get proper format
                    engine = create_engine(get_database_uri())
                    with engine.connect() as connection:
                        query_result = connection.execute(text(sql_query))
                        columns = query_result.keys()
                        rows = query_result.fetchall()
                        results = [dict(zip(columns, row)) for row in rows]
                else:
                    results = result
            except Exception as e:
                logger.warning(f"Error parsing results: {e}")
                results = [{"result": str(result)}]

        logger.info(f"Query executed successfully, {len(results)} rows returned")

        return QueryResponse(
            question=request.question,
            sql_query=sql_query,
            results=results,
            row_count=len(results)
        )

    except SQLAlchemyError as e:
        logger.error(f"Database error: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        logger.error(f"Error processing query: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing query: {str(e)}")

@app.post("/query/sql")
async def execute_sql(
    sql_query: str,
    db: SQLDatabase = Depends(get_db)
):
    """
    Execute raw SQL query (for testing purposes)

    Args:
        sql_query: Raw SQL query to execute

    Returns:
        Query results
    """
    try:
        logger.info(f"Executing SQL: {sql_query}")

        execute_query = QuerySQLDataBaseTool(db=db)
        result = execute_query.invoke(sql_query)

        # Parse results
        results = []
        if result:
            try:
                engine = create_engine(get_database_uri())
                with engine.connect() as connection:
                    query_result = connection.execute(text(sql_query))
                    columns = query_result.keys()
                    rows = query_result.fetchall()
                    results = [dict(zip(columns, row)) for row in rows]
            except Exception as e:
                logger.warning(f"Error parsing results: {e}")
                results = [{"result": str(result)}]

        return {
            "sql_query": sql_query,
            "results": results,
            "row_count": len(results)
        }

    except Exception as e:
        logger.error(f"Error executing SQL: {e}")
        raise HTTPException(status_code=500, detail=f"Error executing SQL: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
