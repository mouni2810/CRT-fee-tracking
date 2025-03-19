import os
import sqlite3
import pandas as pd
from datetime import datetime

def connect_db():
    """Create a connection to the SQLite database"""
    db_path = os.path.join(os.path.dirname(__file__), 'fee_payments.db')
    conn = sqlite3.connect(db_path)
    # Enable foreign keys support
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def setup_db():
    """Set up the database with required tables if they don't exist"""
    conn = connect_db()
    cursor = conn.cursor()
    
    # Create Student table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS student (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        regd_no TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        batch_year TEXT NOT NULL,
        branch TEXT NOT NULL,
        mobile TEXT
    )
    ''')
    
    # Create Payment table with all necessary fields
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS payment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        regd_no TEXT NOT NULL,
        fee_type TEXT NOT NULL,
        batch TEXT,
        amount REAL NOT NULL,
        amount_paid REAL NOT NULL,
        payment_date DATE NOT NULL,
        received_by TEXT NOT NULL,
        remarks TEXT,
        FOREIGN KEY (regd_no) REFERENCES student (regd_no)
    )
    ''')
    
    conn.commit()
    conn.close()

def upsert_student(conn, student_data):
    """Insert or update student record"""
    cursor = conn.cursor()
    
    # Check if student exists
    cursor.execute("SELECT id FROM student WHERE regd_no = ?", (student_data['regd_no'],))
    exists = cursor.fetchone()
    
    if exists:
        # Update existing student
        cursor.execute('''
        UPDATE student 
        SET name = ?, batch_year = ?, branch = ?, mobile = ?
        WHERE regd_no = ?
        ''', (
            student_data['name'], 
            student_data['batch_year'], 
            student_data['branch'], 
            student_data['mobile'], 
            student_data['regd_no']
        ))
    else:
        # Insert new student
        cursor.execute('''
        INSERT INTO student (regd_no, name, batch_year, branch, mobile)
        VALUES (?, ?, ?, ?, ?)
        ''', (
            student_data['regd_no'],
            student_data['name'],
            student_data['batch_year'],
            student_data['branch'],
            student_data['mobile']
        ))
    
    return cursor.rowcount  # Return number of affected rows

def insert_payment(conn, payment_data):
    """Insert payment record"""
    cursor = conn.cursor()
    
    # Insert payment record
    cursor.execute('''
    INSERT INTO payment (regd_no, fee_type, batch, amount, amount_paid, payment_date, received_by, remarks)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        payment_data['regd_no'],
        payment_data['fee_type'],
        payment_data.get('batch', ''),
        payment_data['amount'],
        payment_data['amount_paid'],
        payment_data['payment_date'],
        payment_data['received_by'],
        payment_data.get('remarks', '')
    ))
    
    return cursor.rowcount

def process_excel_file(file_path):
    """Process Excel file and insert data into SQLite database using direct commands"""
    # Check file extension
    _, file_ext = os.path.splitext(file_path)
    
    # Read Excel file
    if file_ext.lower() == '.csv':
        df = pd.read_csv(file_path)
    else:
        df = pd.read_excel(file_path)
    
    # Convert column names to lowercase
    df.columns = [col.lower().strip() for col in df.columns]
    
    # Map column names to expected format
    column_mappings = {
        'registration_number': 'regd_no',
        'registration': 'regd_no',
        'reg_no': 'regd_no',
        'regno': 'regd_no',
        'student_name': 'name',
        'department': 'branch',
        'phone': 'mobile',
        'contact': 'mobile',
        'fee': 'amount',
        'fee_amount': 'amount',
        'payment_date': 'payment_date',
        'date': 'payment_date'
    }
    
    # Apply column mappings
    for old_col, new_col in column_mappings.items():
        if old_col in df.columns and new_col not in df.columns:
            df[new_col] = df[old_col]
    
    # Check required columns
    required_cols = ['regd_no', 'name', 'batch_year', 'branch', 'fee_type', 'amount']
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        raise ValueError(f"Missing required columns: {', '.join(missing_cols)}")
    
    # Connect to database
    conn = connect_db()
    
    try:
        # Start transaction
        conn.execute("BEGIN TRANSACTION")
        
        records_processed = 0
        errors = []
        
        # Process each row
        for idx, row in df.iterrows():
            try:
                # Extract and clean student data
                regd_no = str(row['regd_no']).strip()
                name = str(row['name']).strip()
                batch_year = str(row['batch_year']).strip()
                branch = str(row['branch']).strip()
                mobile = str(row.get('mobile', '')).strip()
                
                # 1. Insert or update student record
                student_data = {
                    'regd_no': regd_no,
                    'name': name,
                    'batch_year': batch_year,
                    'branch': branch,
                    'mobile': mobile
                }
                upsert_student(conn, student_data)
                
                # 2. Process payment data
                fee_type = str(row['fee_type']).strip().lower()
                batch = str(row.get('batch', '')).strip()
                
                # Convert amounts to float
                try:
                    amount = float(row['amount'])
                    amount_paid = float(row.get('paid_amount', 0)) if pd.notna(row.get('paid_amount')) else 0
                    
                    if amount <= 0:
                        raise ValueError("Fee amount must be positive")
                except (ValueError, TypeError) as e:
                    raise ValueError(f"Invalid amount values: {str(e)}")
                
                # Get payment date (use today if not provided)
                payment_date = row.get('payment_date')
                if pd.isna(payment_date):
                    payment_date = datetime.now().strftime('%Y-%m-%d')
                elif isinstance(payment_date, str):
                    payment_date = datetime.strptime(payment_date, '%Y-%m-%d').strftime('%Y-%m-%d')
                elif isinstance(payment_date, (pd.Timestamp, datetime)):
                    payment_date = payment_date.strftime('%Y-%m-%d')
                
                received_by = str(row.get('received_by', 'Excel Import')).strip()
                remarks = str(row.get('remarks', '')).strip()
                
                # Insert payment record
                payment_data = {
                    'regd_no': regd_no,
                    'fee_type': fee_type,
                    'batch': batch,
                    'amount': amount,
                    'amount_paid': amount_paid,
                    'payment_date': payment_date,
                    'received_by': received_by,
                    'remarks': remarks
                }
                
                insert_payment(conn, payment_data)
                records_processed += 1
                
            except Exception as e:
                # Record error, but continue processing other rows
                errors.append(f"Error on row {idx + 2}: {str(e)}")
        
        # Commit transaction if no errors or if errors are acceptable
        if not errors or (records_processed > 0 and len(errors) < records_processed):
            conn.commit()
            print(f"Successfully processed {records_processed} records")
        else:
            # Roll back if too many errors
            conn.rollback()
            print(f"Transaction rolled back due to excessive errors")
    
    except Exception as e:
        conn.rollback()
        print(f"Error processing Excel file: {str(e)}")
        raise
    finally:
        conn.close()
    
    return records_processed, errors

if __name__ == "__main__":
    # Example usage
    setup_db()
    file_path = input("Enter path to Excel file: ")
    if os.path.exists(file_path):
        try:
            records, errors = process_excel_file(file_path)
            print(f"Processed {records} records with {len(errors)} errors")
        except Exception as e:
            print(f"Failed to process file: {str(e)}")
    else:
        print(f"File not found: {file_path}")
