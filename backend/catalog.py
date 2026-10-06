"""상품 DB(SQLite) 조회. AI는 이 모듈을 통해서만 상품을 알 수 있다 (없는 상품 지어내기 방지).

서버를 켤 때마다 data/products.json(쇼핑몰 상품)과 data/market_products.json(시중 제품)으로
data/cosmetic.db 를 새로 만든다. 시중 제품은 출처 URL을 갖고, 확인된 항목만 채운다
(price/volume 은 null 가능, ingredients 는 '주요 성분'이지 전성분이 아니다).
"""
import json
import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "cosmetic.db"
JSON_PATH = DATA_DIR / "products.json"
MARKET_PATH = DATA_DIR / "market_products.json"

SKIN_TYPES = ["건성", "지성", "복합성", "민감성"]

_SCHEMA = """
CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    skin_type TEXT NOT NULL,
    price INTEGER,                -- 시중 제품은 모르면 NULL
    volume TEXT,
    rating REAL,
    review_count INTEGER,
    ingredients TEXT NOT NULL,   -- JSON 배열 (시중 제품은 주요 성분만)
    description TEXT,
    stock INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'shop',   -- 'shop' 쇼핑몰 상품 / 'market' 시중 제품
    brand TEXT,
    source_url TEXT
);
CREATE INDEX idx_products_filter ON products (category, skin_type, price);
"""


def _init_db() -> None:
    shop = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    market = json.loads(MARKET_PATH.read_text(encoding="utf-8")) if MARKET_PATH.exists() else []
    rows = [
        (p["id"], p["name"], p["category"], p["skinType"], p["price"], p.get("volume"),
         p.get("rating"), p.get("reviewCount"), json.dumps(p["ingredients"], ensure_ascii=False),
         p.get("desc"), p["stock"], "shop", None, None)
        for p in shop
    ] + [
        (p["id"], p["name"], p["category"], p["skinType"], p.get("price"), p.get("volume"),
         None, None, json.dumps(p["ingredients"], ensure_ascii=False),
         p.get("desc"), 1, "market", p.get("brand"), p.get("url"))
        for p in market
    ]
    tmp = DB_PATH.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(_SCHEMA)
    con.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    tmp.replace(DB_PATH)


_init_db()


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)  # 요청마다 새 연결 (스레드 안전)
    con.row_factory = sqlite3.Row
    return con


def _view(row: sqlite3.Row) -> dict:
    """모델에게 보여줄 필드만 추린다."""
    return {
        "id": row["id"], "name": row["name"], "category": row["category"],
        "skinType": row["skin_type"], "price": row["price"], "volume": row["volume"],
        "rating": row["rating"], "reviewCount": row["review_count"],
        "ingredients": json.loads(row["ingredients"]), "desc": row["description"],
        "source": row["kind"], "brand": row["brand"], "url": row["source_url"],
        # 시중 제품은 전성분이 아니라 주요 성분만 알고 있다
        "ingredientsAreKeyOnly": row["kind"] == "market",
    }


def categories() -> list[str]:
    with _connect() as con:
        return [r[0] for r in con.execute("SELECT DISTINCT category FROM products ORDER BY 1")]


def count() -> int:
    with _connect() as con:
        return con.execute("SELECT COUNT(*) FROM products").fetchone()[0]


def get_product(product_id: int) -> dict | None:
    with _connect() as con:
        row = con.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    return _view(row) if row else None


def search_products(
    skin_type: str | None = None,
    category: str | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    include_ingredients: list[str] | None = None,
    exclude_ingredients: list[str] | None = None,
    keyword: str | None = None,
    source: str | None = None,
    limit: int = 6,
) -> list[dict]:
    where, args = ["stock > 0"], []
    if source in ("shop", "market"):
        where.append("kind = ?")
        args.append(source)
    if skin_type:
        where.append("skin_type IN (?, '전체')")  # '전체'는 모든 피부 타입용
        args.append(skin_type)
    if category:
        where.append("category = ?")
        args.append(category)
    if min_price is not None:
        where.append("price >= ?")
        args.append(min_price)
    if max_price is not None:
        where.append("price <= ?")
        args.append(max_price)
    for ing in include_ingredients or []:
        where.append("ingredients LIKE ?")
        args.append(f"%{ing}%")
    for ing in exclude_ingredients or []:
        where.append("ingredients NOT LIKE ?")
        args.append(f"%{ing}%")
    if exclude_ingredients:
        # 시중 제품은 주요 성분만 알아서 '들어있지 않다'고 보증할 수 없다 -> 제외
        where.append("kind = 'shop'")
    for word in (keyword or "").split():  # 단어마다 이름/설명/성분 중 어딘가에 있어야 함
        where.append("(name LIKE ? OR description LIKE ? OR ingredients LIKE ?)")
        args += [f"%{word}%"] * 3
    sql = (
        f"SELECT * FROM products WHERE {' AND '.join(where)} "
        "ORDER BY (rating IS NULL), rating DESC, review_count DESC LIMIT ?"
    )
    args.append(max(1, min(int(limit), 10)))
    with _connect() as con:
        return [_view(r) for r in con.execute(sql, args)]
