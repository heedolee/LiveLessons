# LangChain SQL Generator with FastAPI

FastAPI 애플리케이션으로, LangChain 1.0.0을 사용하여 자연어를 SQL 쿼리로 변환하고 MariaDB에서 실행하는 기능을 제공합니다.

## 주요 기능

- 🔍 자연어를 SQL 쿼리로 자동 변환
- 🗄️ MariaDB 스키마 자동 로드
- 📝 스키마 설명 파일(sample.txt) 기반 컨텍스트 제공
- 🚀 RESTful API를 통한 쉬운 통합
- 📊 프론트엔드로 결과 전달

## 기술 스택

- **FastAPI**: 고성능 웹 프레임워크
- **LangChain 1.0.0**: LLM 기반 애플리케이션 프레임워크
- **OpenAI GPT-4**: 자연어 처리 및 SQL 생성
- **MariaDB**: 관계형 데이터베이스
- **SQLAlchemy**: 데이터베이스 ORM

## 설치 방법

### 1. 의존성 설치

```bash
pip install -r requirements.txt
```

### 2. 환경 변수 설정

`.env.example` 파일을 `.env`로 복사하고 설정을 입력합니다:

```bash
cp .env.example .env
```

`.env` 파일 수정:

```env
# MariaDB 설정
MARIADB_HOST=localhost
MARIADB_PORT=3306
MARIADB_USER=root
MARIADB_PASSWORD=your_password
MARIADB_DATABASE=testdb

# OpenAI API 키
OPENAI_API_KEY=sk-your-api-key-here
LLM_MODEL=gpt-4
LLM_TEMPERATURE=0

# 스키마 파일
SCHEMA_FILE=sample.txt
```

### 3. 데이터베이스 준비

MariaDB에 테스트 데이터베이스와 테이블을 생성합니다:

```sql
CREATE DATABASE testdb;
USE testdb;

CREATE TABLE users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100),
    email VARCHAR(100),
    age INT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(20)
);

CREATE TABLE products (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(200),
    description TEXT,
    price DECIMAL(10,2),
    stock INT,
    category VARCHAR(50),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE orders (
    id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT,
    product_id INT,
    quantity INT,
    total_price DECIMAL(10,2),
    order_date DATETIME DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(20),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (product_id) REFERENCES products(id)
);

-- 샘플 데이터 삽입
INSERT INTO users (name, email, age, status) VALUES
('김철수', 'kim@example.com', 30, 'active'),
('이영희', 'lee@example.com', 25, 'active'),
('박민수', 'park@example.com', 35, 'inactive');

INSERT INTO products (name, description, price, stock, category) VALUES
('노트북', '고성능 노트북', 1500000, 10, '전자제품'),
('마우스', '무선 마우스', 30000, 50, '전자제품'),
('키보드', '기계식 키보드', 120000, 30, '전자제품');

INSERT INTO orders (user_id, product_id, quantity, total_price, status) VALUES
(1, 1, 1, 1500000, 'completed'),
(2, 2, 2, 60000, 'completed'),
(1, 3, 1, 120000, 'pending');
```

## 실행 방법

### 개발 서버 실행

```bash
python main.py
```

또는

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

서버가 실행되면 다음 주소에서 접근할 수 있습니다:
- API: http://localhost:8000
- API 문서 (Swagger): http://localhost:8000/docs
- API 문서 (ReDoc): http://localhost:8000/redoc

## API 엔드포인트

### 1. Health Check

```bash
GET /health
```

서버 상태와 데이터베이스 연결 상태를 확인합니다.

### 2. 스키마 조회

```bash
GET /schema
```

데이터베이스 스키마 정보와 sample.txt의 설명을 조회합니다.

### 3. 자연어 쿼리 (메인 기능)

```bash
POST /query
Content-Type: application/json

{
  "question": "활성 사용자의 이름과 이메일을 보여줘"
}
```

응답:

```json
{
  "question": "활성 사용자의 이름과 이메일을 보여줘",
  "sql_query": "SELECT name, email FROM users WHERE status = 'active'",
  "results": [
    {
      "name": "김철수",
      "email": "kim@example.com"
    },
    {
      "name": "이영희",
      "email": "lee@example.com"
    }
  ],
  "row_count": 2
}
```

### 4. SQL 직접 실행

```bash
POST /query/sql?sql_query=SELECT * FROM users
```

## 사용 예시

### cURL

```bash
# Health check
curl http://localhost:8000/health

# 자연어 쿼리
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "30세 이상 사용자의 수를 알려줘"}'

# 스키마 조회
curl http://localhost:8000/schema
```

### Python

```python
import requests

# 자연어 쿼리
response = requests.post(
    "http://localhost:8000/query",
    json={"question": "지난달 총 매출을 보여줘"}
)
result = response.json()
print(f"SQL: {result['sql_query']}")
print(f"Results: {result['results']}")
```

### JavaScript (Fetch API)

```javascript
// 자연어 쿼리
fetch('http://localhost:8000/query', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    question: '재고가 10개 미만인 제품을 보여줘'
  })
})
.then(response => response.json())
.then(data => {
  console.log('SQL:', data.sql_query);
  console.log('Results:', data.results);
});
```

## 프로젝트 구조

```
langchain-sql-generator/
├── main.py              # FastAPI 애플리케이션
├── sample.txt           # 데이터베이스 스키마 설명
├── requirements.txt     # Python 의존성
├── .env.example         # 환경 변수 예시
└── README.md           # 프로젝트 문서
```

## 스키마 설명 파일 (sample.txt)

`sample.txt` 파일은 데이터베이스 스키마에 대한 상세한 설명을 제공합니다. 이 정보는 LLM이 더 정확한 SQL 쿼리를 생성하는 데 도움을 줍니다.

파일 형식:
- 각 테이블의 컬럼과 데이터 타입
- 컬럼의 의미와 용도
- 테이블 간 관계 (Foreign Key)
- 일반적인 쿼리 패턴

## 자연어 쿼리 예시

- "모든 사용자를 보여줘"
- "활성 상태인 사용자의 수는?"
- "가장 비싼 제품 5개를 보여줘"
- "김철수의 주문 내역을 보여줘"
- "전자제품 카테고리의 총 재고는?"
- "완료된 주문의 총 매출액은?"
- "30세 이상 사용자들의 평균 주문 금액은?"

## 주의사항

1. **보안**: 프로덕션 환경에서는 적절한 인증 및 권한 관리를 구현하세요.
2. **API 키**: OpenAI API 키를 안전하게 관리하세요 (환경 변수 사용).
3. **SQL 인젝션**: 사용자 입력을 직접 SQL에 삽입하지 않도록 주의하세요.
4. **비용**: OpenAI API 사용에 따른 비용이 발생합니다.
5. **쿼리 제한**: 대량의 데이터를 반환하는 쿼리는 제한하는 것이 좋습니다.

## 문제 해결

### 데이터베이스 연결 오류

```
Database connection failed
```

해결 방법:
- MariaDB가 실행 중인지 확인
- `.env` 파일의 연결 정보가 올바른지 확인
- 방화벽 설정 확인

### OpenAI API 오류

```
OPENAI_API_KEY environment variable is not set
```

해결 방법:
- `.env` 파일에 올바른 OpenAI API 키 설정
- API 키의 유효성 확인

## 라이센스

MIT License

## 기여

이슈와 풀 리퀘스트를 환영합니다!
