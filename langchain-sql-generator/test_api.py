"""
Test script for LangChain SQL Generator API
"""
import requests
import json
from typing import Dict, Any

# API Base URL
BASE_URL = "http://localhost:8000"

def print_response(title: str, response: requests.Response):
    """Print formatted response"""
    print(f"\n{'='*60}")
    print(f"{title}")
    print(f"{'='*60}")
    print(f"Status Code: {response.status_code}")
    try:
        data = response.json()
        print(json.dumps(data, indent=2, ensure_ascii=False))
    except:
        print(response.text)
    print()

def test_health_check():
    """Test health check endpoint"""
    response = requests.get(f"{BASE_URL}/health")
    print_response("Health Check", response)
    return response.status_code == 200

def test_schema():
    """Test schema endpoint"""
    response = requests.get(f"{BASE_URL}/schema")
    print_response("Schema Information", response)
    return response.status_code == 200

def test_natural_language_query(question: str):
    """Test natural language to SQL conversion"""
    response = requests.post(
        f"{BASE_URL}/query",
        json={"question": question}
    )
    print_response(f"Query: {question}", response)
    return response.status_code == 200

def test_direct_sql(sql_query: str):
    """Test direct SQL execution"""
    response = requests.post(
        f"{BASE_URL}/query/sql",
        params={"sql_query": sql_query}
    )
    print_response(f"Direct SQL: {sql_query}", response)
    return response.status_code == 200

def main():
    """Run all tests"""
    print("🚀 Starting API Tests")
    print(f"Target: {BASE_URL}")

    # Test 1: Health Check
    print("\n📊 Test 1: Health Check")
    if not test_health_check():
        print("❌ Health check failed. Make sure the server is running.")
        return

    # Test 2: Schema
    print("\n📊 Test 2: Schema Information")
    test_schema()

    # Test 3: Natural Language Queries
    print("\n📊 Test 3: Natural Language Queries")

    queries = [
        "모든 사용자를 보여줘",
        "활성 상태인 사용자는 몇 명이야?",
        "가장 비싼 제품 3개를 보여줘",
        "김철수의 모든 주문 내역을 보여줘",
        "전자제품 카테고리의 총 재고는?",
        "완료된 주문의 총 매출액은?",
        "30세 이상 사용자들의 평균 나이는?",
    ]

    for i, query in enumerate(queries, 1):
        print(f"\n--- Query {i}/{len(queries)} ---")
        test_natural_language_query(query)

    # Test 4: Direct SQL
    print("\n📊 Test 4: Direct SQL Execution")
    test_direct_sql("SELECT COUNT(*) as total_users FROM users")

    print("\n✅ All tests completed!")

if __name__ == "__main__":
    main()
