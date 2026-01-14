-- Initialize database schema and sample data

CREATE TABLE IF NOT EXISTS users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    age INT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(20) DEFAULT 'active'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS products (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    price DECIMAL(10,2) NOT NULL,
    stock INT DEFAULT 0,
    category VARCHAR(50),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS orders (
    id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT NOT NULL,
    product_id INT NOT NULL,
    quantity INT NOT NULL DEFAULT 1,
    total_price DECIMAL(10,2) NOT NULL,
    order_date DATETIME DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(20) DEFAULT 'pending',
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Insert sample users
INSERT INTO users (name, email, age, status) VALUES
('김철수', 'kim.chulsoo@example.com', 30, 'active'),
('이영희', 'lee.younghee@example.com', 25, 'active'),
('박민수', 'park.minsoo@example.com', 35, 'inactive'),
('최지은', 'choi.jieun@example.com', 28, 'active'),
('정태양', 'jung.taeyang@example.com', 42, 'active');

-- Insert sample products
INSERT INTO products (name, description, price, stock, category) VALUES
('노트북', '고성능 게이밍 노트북 RTX 4090', 2500000, 15, '전자제품'),
('마우스', '무선 게이밍 마우스', 89000, 50, '전자제품'),
('키보드', '기계식 RGB 키보드', 159000, 30, '전자제품'),
('모니터', '32인치 4K 모니터', 650000, 20, '전자제품'),
('헤드셋', '노이즈 캔슬링 헤드셋', 320000, 25, '전자제품'),
('의자', '게이밍 체어', 450000, 10, '가구'),
('책상', '높이 조절 책상', 380000, 8, '가구'),
('USB 케이블', 'USB-C to USB-C 케이블', 25000, 100, '액세서리');

-- Insert sample orders
INSERT INTO orders (user_id, product_id, quantity, total_price, status, order_date) VALUES
(1, 1, 1, 2500000, 'completed', DATE_SUB(NOW(), INTERVAL 10 DAY)),
(1, 2, 1, 89000, 'completed', DATE_SUB(NOW(), INTERVAL 10 DAY)),
(2, 3, 1, 159000, 'completed', DATE_SUB(NOW(), INTERVAL 8 DAY)),
(2, 8, 3, 75000, 'completed', DATE_SUB(NOW(), INTERVAL 7 DAY)),
(3, 4, 1, 650000, 'cancelled', DATE_SUB(NOW(), INTERVAL 5 DAY)),
(4, 5, 1, 320000, 'completed', DATE_SUB(NOW(), INTERVAL 4 DAY)),
(4, 6, 1, 450000, 'completed', DATE_SUB(NOW(), INTERVAL 3 DAY)),
(5, 7, 1, 380000, 'pending', DATE_SUB(NOW(), INTERVAL 2 DAY)),
(1, 8, 2, 50000, 'completed', DATE_SUB(NOW(), INTERVAL 1 DAY)),
(2, 2, 2, 178000, 'pending', NOW());

-- Create indexes for better performance
CREATE INDEX idx_users_status ON users(status);
CREATE INDEX idx_products_category ON products(category);
CREATE INDEX idx_orders_user_id ON orders(user_id);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_order_date ON orders(order_date);
