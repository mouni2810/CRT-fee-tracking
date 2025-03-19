"""
Database initialization script.
Run this script once to create the database tables with the correct schema.
"""
import os
import sqlite3
from flask import Flask
from models import db, init_db, Student, Payment
        
def initialize_database():
    """Initialize the database with proper schema"""
    print("Initializing database...")
    
    # Check if database file exists
    db_path = os.path.join(os.path.dirname(__file__), 'fee_payments.db')
    if os.path.exists(db_path):
        print(f"Removing existing database at {db_path}")
        os.remove(db_path)
    
    # Create a minimal Flask application
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///fee_payments.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    # Initialize database
    init_db(app)
    
    # Create all tables
    with app.app_context():
        db.create_all()
        print("Database tables created successfully!")
    
    # Verify schema
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # List all tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    print("\nCreated tables:")
    for table in tables:
        print(f"- {table[0]}")
    
    # Verify payment table columns
    cursor.execute("PRAGMA table_info(payment)")
    columns = cursor.fetchall()
    print("\nColumns in payment table:")
    for col in columns:
        print(f"- {col[1]} ({col[2]})")
    
    # Show success message
    print("\nDatabase initialization complete!")
    print("You should now be able to run the application without errors.")
    print("\nImportant: Use the upload page to import data from Excel files.")
    
    conn.close()

if __name__ == "__main__":
    initialize_database()
