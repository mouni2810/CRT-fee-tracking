import os
from flask import Flask
from models import db, init_db, Student, FeeMaster, Payment
import sqlite3

# Create a minimal Flask application for database initialization
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///fee_payments.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize the database
init_db(app)

def check_full_schema():
    """Check if the database schema completely matches our models"""
    db_path = os.path.join(os.path.dirname(__file__), 'fee_payments.db')
    if not os.path.exists(db_path):
        return False, "Database file doesn't exist"
    
    try:
        # Connect to the database directly
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if required tables exist
        required_tables = ['student', 'fee_master', 'payment']
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        existing_tables = [row[0] for row in cursor.fetchall() if not row[0].startswith('sqlite_')]
        
        for table in required_tables:
            if table not in existing_tables:
                conn.close()
                return False, f"Missing required table: {table}"
        
        # Check fee_master table columns
        cursor.execute("PRAGMA table_info(fee_master)")
        columns = {col[1]: col for col in cursor.fetchall()}
        
        required_columns = ['id', 'regd_no', 'fee_type', 'amount', 'batch', 'remarks']
        for column in required_columns:
            if column not in columns:
                conn.close()
                return False, f"Missing column in fee_master table: {column}"
        
        conn.close()
        return True, "Schema is correct"
    except Exception as e:
        return False, f"Error checking schema: {e}"

def create_tables():
    """Create all database tables"""
    with app.app_context():
        # Drop all existing tables first to ensure clean schema
        db.drop_all()
        # Create all tables defined in models.py
        db.create_all()
        print("Database tables created successfully!")

if __name__ == "__main__":
    # Check if database file exists
    db_path = os.path.join(os.path.dirname(__file__), 'fee_payments.db')
    schema_correct, message = check_full_schema()
    
    print("Fee Payments Database Setup")
    print("==========================")
    
    if os.path.exists(db_path):
        if not schema_correct:
            print(f"Database exists at: {db_path} but has schema issues!")
            print(f"Issue: {message}")
            print("The database schema doesn't match the current models.")
            
            # Force recreation due to schema issues
            print("Recreating database with correct schema...")
            os.remove(db_path)
            create_tables()
            print("Database recreated successfully!")
        else:
            print(f"Database exists at: {db_path} with correct schema.")
            force_recreate = input("Do you want to force-recreate the database anyway? (y/n): ")
            if force_recreate.lower() == 'y':
                os.remove(db_path)
                print("Database file deleted.")
                create_tables()
                print("Database recreated successfully!")
            else:
                print("Database kept intact.")
    else:
        print(f"Creating new database at: {db_path}")
        create_tables()
        print("Database created successfully!")
    
    # Verify the database was created successfully
    print("\nVerifying database...")
    schema_ok, verify_message = check_full_schema()
    if schema_ok:
        print("✓ Database verification successful!")
        print("✓ All required tables and columns exist.")
        print("\nYou can now run the application with: python app.py")
    else:
        print("✗ Database verification failed!")
        print(f"Error: {verify_message}")
