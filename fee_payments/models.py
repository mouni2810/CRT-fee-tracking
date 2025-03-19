from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

# Student model
class Student(db.Model):
    __tablename__ = 'student'
    
    id = db.Column(db.Integer, primary_key=True)
    regd_no = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    batch_year = db.Column(db.String(10), nullable=False)
    branch = db.Column(db.String(50), nullable=False)
    mobile = db.Column(db.String(15), nullable=True)
    # Define relationships to other tables
    fees = db.relationship('FeeMaster', backref='student', lazy=True)
    payments = db.relationship('Payment', backref='student', lazy=True)
    
    def __repr__(self):
        return f"<Student {self.regd_no}: {self.name}>"

# Fee model
class FeeMaster(db.Model): 
    __tablename__ = 'fee_master'
    
    id = db.Column(db.Integer, primary_key=True)
    regd_no = db.Column(db.String(20), db.ForeignKey('student.regd_no'), nullable=False)
    fee_type = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    remarks = db.Column(db.Text, nullable=True)
    
    def __repr__(self):
        return f"<FeeMaster {self.id}: {self.fee_type} - {self.amount}>"

class Payment(db.Model):
    __tablename__ = 'payment'
    
    id = db.Column(db.Integer, primary_key=True)
    regd_no = db.Column(db.String(20), db.ForeignKey('student.regd_no'), nullable=False)
    batch_year = db.Column(db.String(20), nullable=False)
    fee_type = db.Column(db.String(100), nullable=False)
    amount_paid = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, nullable=False)
    received_by = db.Column(db.String(100), nullable=False)
    
    def __repr__(self):
        return f"<Payment {self.regd_no}: {self.fee_type} - ₹{self.amount_paid}>"

# Admin model for authentication
class Admin(db.Model, UserMixin):
    __tablename__ = 'admin'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    last_login = db.Column(db.DateTime, default=None)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    @staticmethod
    def create_default_admin(db_session):
        """Create a default admin user if none exists"""
        try:
            if not Admin.query.filter_by(username='admin').first():
                default_admin = Admin(username="admin")
                default_admin.set_password("adminpass")
                db_session.add(default_admin)
                db_session.commit()
                print("Default admin created successfully")
                return default_admin
            return None
        except Exception as e:
            print(f"Error creating default admin: {str(e)}")
            db_session.rollback()
            return None

# Function to initialize the database with the app
def init_db(app):
    db.init_app(app)
    with app.app_context():
        db.create_all()
        # Create default admin user
        print("Attempting to create default admin...")
        Admin.create_default_admin(db.session)
