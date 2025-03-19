# Fee Management System

## Overview

The Fee Management System is a web application designed to manage student fee payments. It allows administrators to upload student fee data, track payments, and generate reports on unpaid fees. The system provides various features such as filtering student records, viewing payment details, and downloading reports.

## Features

- Upload student fee data via Excel or CSV files
- Track payments for different fee types (e.g., CRT, Phase 2, Phase 3)
- Filter student records by batch year, registration number, branch, and payment status
- View detailed payment status for each student
- Generate and download reports on unpaid fees
- Admin login and session management

## Setup Instructions

### Prerequisites

- Python 3.7 or higher
- Flask
- SQLAlchemy
- Pandas
- Matplotlib
- Flask-Login

### Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/Neeharika2/feetrack.git
   cd feetrack/fee_payments
   ```

2. Create a virtual environment and activate it:

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
   ```

3. Install the required packages:

   ```bash
   pip install -r requirements.txt
   ```

4. Initialize the database:

   ```bash
   python -c "from models import init_db; init_db()"
   ```

5. Run the application:

   ```bash
   flask run
   ```

   The application will be available at `http://127.0.0.1:5000`.

### Configuration

- The database URI and other configurations can be modified in the `app.py` file.
- The secret key for session management is generated automatically but can be set manually in the `app.py` file.

## Usage

### Admin Login

1. Navigate to the admin login page: `http://127.0.0.1:5000/admin/login`
2. Enter the admin credentials to log in.

### Uploading Fee Data

1. Go to the Upload page: `http://127.0.0.1:5000/upload`
2. Select an Excel or CSV file containing student fee data.
3. Click the "Upload" button to process the file.

### Viewing Student Details

1. Go to the Student Details page: `http://127.0.0.1:5000/student_details`
2. Use the filters to search for specific student records.
3. Click on a student's name to view detailed payment information.

### Generating Reports

1. Go to the Download page: `http://127.0.0.1:5000/unpaid_students`
2. Use the filters to select the criteria for the report.
3. Click the "Download CSV" button to download the report.

## Contributing

Contributions are welcome! Please fork the repository and create a pull request with your changes.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Contact

For any questions or issues, please contact [neeharikagudipudi@gmail.com](mailto:neeharikagudipudi@gmail.com).
