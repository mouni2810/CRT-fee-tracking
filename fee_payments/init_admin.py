from app import app, db
from models import Admin
import getpass

def create_admin_user():
    """Create an admin user interactively"""
    print("=== Creating Admin User ===")
    print("1. Create default admin (username: admin, password: adminpass)")
    print("2. Create custom admin user")
    
    choice = input("Enter your choice (1/2): ")
    
    if choice == "1":
        with app.app_context():
            admin = Admin.create_default_admin(db.session)
            if admin:
                print("Default admin user created successfully!")
                print("Username: admin")
                print("Password: adminpass")
            else:
                print("Default admin already exists!")
                
    elif choice == "2":
        username = input("Enter admin username: ")
        
        # Check if user already exists
        with app.app_context():
            existing_user = Admin.query.filter_by(username=username).first()
            if existing_user:
                print(f"Error: Admin '{username}' already exists!")
                return False
        
        # Get password securely
        password = getpass.getpass("Enter admin password: ")
        confirm = getpass.getpass("Confirm password: ")
        
        if password != confirm:
            print("Error: Passwords don't match!")
            return False
        
        # Create user
        with app.app_context():
            admin = Admin(username=username)
            admin.set_password(password)
            db.session.add(admin)
            db.session.commit()
            print(f"Success! Admin user '{username}' created.")
            return True
    else:
        print("Invalid choice. Exiting...")

if __name__ == "__main__":
    create_admin_user()
