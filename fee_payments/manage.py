from app import app, db  # Import your app and db from wherever they're defined

def clear_test_database():
    with app.app_context():
        db.drop_all()
        db.create_all()
        print("Test database cleared!")

if __name__ == "__main__":
    clear_test_database()
