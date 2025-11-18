import pymysql

# Connection settings
HOST = "175.120.35.228"
USER = "root"
PASSWORD = "root"
PORT = 3306

# Database/table settings
DB_NAME = "kkanbu"
TABLE_NAME = "demo"


def create_table(connection):
    """기존 kkanbu DB에 테이블 생성"""
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS `{TABLE_NAME}` (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        print(f"테이블 '{TABLE_NAME}' 생성 완료 (또는 이미 존재)")


def insert_sample_rows(connection):
    """샘플 데이터 삽입"""
    with connection.cursor() as cursor:
        cursor.execute(f"INSERT INTO `{TABLE_NAME}` (name) VALUES (%s)", ("Alice",))
        cursor.execute(f"INSERT INTO `{TABLE_NAME}` (name) VALUES (%s)", ("Bob",))
        print("샘플 데이터 삽입 완료")


def fetch_recent_rows(connection, limit=5):
    """최근 데이터 조회"""
    with connection.cursor(pymysql.cursors.DictCursor) as cursor:
        cursor.execute(f"SELECT * FROM `{TABLE_NAME}` ORDER BY id DESC LIMIT %s", (limit,))
        return cursor.fetchall()


def main():
    # 기존 kkanbu DB에 직접 접속
    connection = pymysql.connect(
        host=HOST,
        user=USER,
        password=PASSWORD,
        port=PORT,
        database=DB_NAME,  # 연결 시 DB 지정
        autocommit=True,
        connect_timeout=10,
    )

    try:
        print(f"'{DB_NAME}' DB에 연결되었습니다.")
        create_table(connection)
        insert_sample_rows(connection)
        rows = fetch_recent_rows(connection, limit=5)
        print("\n최근 데이터:")
        for row in rows:
            print(row)
    except pymysql.Error as e:
        print(f"데이터베이스 오류 발생: {e}")
    finally:
        connection.close()
        print(f"\n'{DB_NAME}' DB 연결 종료")


if __name__ == "__main__":
    main()