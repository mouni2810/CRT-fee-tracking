from xml.parsers.expat import errors
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, make_response, session, Response
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from functools import wraps
import os
import pandas as pd
import numpy as np
from io import BytesIO
import base64
from datetime import datetime, timedelta
import threading
import re

# Import database models
from models import db, init_db, Student, FeeMaster, Payment, Admin

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.urandom(24)  # Secret key for flash messages

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///fee_payments.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)  # For "remember me" functionality

# Initialize database
init_db(app)

# Initialize login manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'admin_login'
login_manager.login_message = 'Please log in as admin to access this page.'
login_manager.login_message_category = 'info'

@login_manager.user_loader
def load_user(user_id):
    return Admin.query.get(int(user_id))

# Utility function to normalize fee types
def normalize_fee_type(fee_type):
    # First standardize the input
    if not fee_type:
        return ""
    fee_type = fee_type.lower().strip()
    
    # Map to standard names - FIXED order to check Phase 3 first before Phase 2
    if 'crt' in fee_type:
        return 'crt fee'
    elif any(x in fee_type for x in ['phase 3', 'phase-3', 'phase-iii', 'phase iii']):
        return 'smart interviews phase-iii'
    elif any(x in fee_type for x in ['phase 2', 'phase-2', 'phase-ii', 'phase ii']):
        return 'smart interviews phase-ii'
    return fee_type

# Function to standardize fee type labels for display
def get_standardized_fee_type_label(fee_type):
    fee_type = fee_type.lower()
    if 'crt' in fee_type:
        return 'CRT'
    elif 'phase-iii' in fee_type or 'phase 3' in fee_type:
        return 'Phase 3'
    elif 'phase-ii' in fee_type or 'phase 2' in fee_type:
        return 'Phase 2'
    else:
        return fee_type.title()  # Default case, capitalize words

# Function to create visualizations

def get_payment_status_by_fee_type():
    fee_type_stats = {}
    
    try:
        # Get all distinct batch years
        batch_years = [year[0] for year in db.session.query(Student.batch_year).distinct().order_by(Student.batch_year).all()]
        
        # Initialize the stats dictionary with batch year breakdowns
        for fee_type in ['CRT', 'Phase 2', 'Phase 3']:
            fee_type_stats[fee_type] = {
                'total': 0, 
                'paid': 0, 
                'partially_paid': 0, 
                'not_paid': 0,
                'by_batch': {year: {'total': 0, 'paid': 0, 'partially_paid': 0, 'not_paid': 0} for year in batch_years}
            }
        
        # Get all students with fee entries
        students = db.session.query(Student.regd_no, Student.batch_year).join(
            FeeMaster, FeeMaster.regd_no == Student.regd_no
        ).distinct().all()
        
        # Log how many students we're processing
        app.logger.info(f"Processing payment status for {len(students)} students across all batches")
        
        # Process each student
        for student in students:
            regd_no = student.regd_no
            batch_year = student.batch_year
            
            # Get all fee entries for this student
            fee_entries = db.session.query(
                FeeMaster.fee_type,
                FeeMaster.amount,
                FeeMaster.remarks
            ).filter(FeeMaster.regd_no == regd_no).all()
            
            # Process each fee entry
            for entry in fee_entries:
                std_fee_type = get_standardized_fee_type_label(entry.fee_type)
                
                # Skip if not one of our standard fee types
                if std_fee_type not in fee_type_stats:
                    continue
                
                # Get payment info for this fee type
                normalized_entry_type = normalize_fee_type(entry.fee_type)
                payment = db.session.query(
                    db.func.sum(Payment.amount_paid).label('total_paid')
                ).filter(
                    Payment.regd_no == regd_no,
                    db.func.lower(Payment.fee_type).like(f'%{normalized_entry_type}%')
                ).first()
                
                # Calculate payment status using same logic as student details
                total_paid = payment.total_paid if payment and payment.total_paid else 0
                entry_amount = float(entry.amount)
                
                # Increment total counter
                fee_type_stats[std_fee_type]['total'] += 1
                if batch_year in fee_type_stats[std_fee_type]['by_batch']:
                    fee_type_stats[std_fee_type]['by_batch'][batch_year]['total'] += 1
                
                # Determine status with same epsilon logic as student details
                epsilon = 0.01  # Allow for small floating point differences
                if total_paid >= (entry_amount - epsilon):
                    fee_type_stats[std_fee_type]['paid'] += 1
                    if batch_year in fee_type_stats[std_fee_type]['by_batch']:
                        fee_type_stats[std_fee_type]['by_batch'][batch_year]['paid'] += 1
                elif total_paid > 0:
                    fee_type_stats[std_fee_type]['partially_paid'] += 1
                    if batch_year in fee_type_stats[std_fee_type]['by_batch']:
                        fee_type_stats[std_fee_type]['by_batch'][batch_year]['partially_paid'] += 1
                else:
                    fee_type_stats[std_fee_type]['not_paid'] += 1
                    if batch_year in fee_type_stats[std_fee_type]['by_batch']:
                        fee_type_stats[std_fee_type]['by_batch'][batch_year]['not_paid'] += 1
        
        # Log the stats before returning
        for fee_type, stats in fee_type_stats.items():
            app.logger.info(f"{fee_type} stats: Total={stats['total']}, Paid={stats['paid']}, "
                           f"Partially Paid={stats['partially_paid']}, Not Paid={stats['not_paid']}")
                    
        return fee_type_stats
    except Exception as e:
        app.logger.error(f"Error getting payment status by fee type: {str(e)}")
        import traceback
        app.logger.error(traceback.format_exc())
        return fee_type_stats  # Return empty stats on error

@app.route('/')
def index():
    # Redirect to dashboard if logged in, otherwise to admin login page
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('admin_login'))

def create_visualizations(filters=None):
    # Configure matplotlib to use a non-interactive backend
    import matplotlib
    matplotlib.use('Agg')  # Use Agg backend which is thread-safe
    import matplotlib.pyplot as plt
    import seaborn as sns
    
    charts = {}
    
    try:
        # Chart 2: Fee type-wise Payment Status by Batch Year - IMPROVED IMPLEMENTATION
        # Create a SINGLE graph combining paid and unpaid students by fee type and batch year
        plt.figure(figsize=(14, 10))  # Larger figure for better visibility

        try:
            fee_status_data = get_payment_status_by_fee_type()
            
            # ...existing code...
        
        except Exception as e:
            app.logger.error(f"Error generating payment status chart: {str(e)}")
            import traceback
            app.logger.error(traceback.format_exc())
            plt.text(0.5, 0.5, f'Error: {str(e)}', 
                    ha='center', va='center', fontsize=14, transform=plt.gca().transAxes)
            plt.gca().set_axis_off()
        
        # ...existing code...
        # Chart 2: Fee type-wise Payment Status by Batch Year - IMPROVED IMPLEMENTATION
        # Create a SINGLE graph combining paid and unpaid students by fee type and batch year
        plt.figure(figsize=(14, 10))  # Larger figure for better visibility

        try:
            fee_status_data = get_payment_status_by_fee_type()
            
            # Debug logging
            app.logger.info(f"Fee status data: {fee_status_data}")
            
            # Get all batch years
            all_batch_years = []
            for fee_type, stats in fee_status_data.items():
                if 'by_batch' in stats:
                    all_batch_years.extend(list(stats['by_batch'].keys()))
            all_batch_years = sorted(list(set(all_batch_years)))
            
            # Create DataFrames for consolidated view (paid vs unpaid)
            payment_status_data = []
            
            for fee_type, stats in fee_status_data.items():
                if stats['total'] > 0 and 'by_batch' in stats:
                    for batch_year in all_batch_years:
                        if batch_year in stats['by_batch']:
                            batch_stats = stats['by_batch'][batch_year]
                            
                            # Combine fully paid and partially paid into one "paid" category
                            paid_count = batch_stats['paid'] + batch_stats['partially_paid']
                            not_paid_count = batch_stats['not_paid']
                            
                            # Add data for paid students
                            if paid_count > 0:
                                payment_status_data.append({
                                    'Fee Type': fee_type,
                                    'Batch Year': batch_year,
                                    'Status': 'Paid',
                                    'Count': paid_count
                                })
                            
                            # Add data for unpaid students
                            if not_paid_count > 0:
                                payment_status_data.append({
                                    'Fee Type': fee_type,
                                    'Batch Year': batch_year,
                                    'Status': 'Unpaid',
                                    'Count': not_paid_count
                                })
            
            # Convert to DataFrame
            status_df = pd.DataFrame(payment_status_data) if payment_status_data else pd.DataFrame(columns=['Fee Type', 'Batch Year', 'Status', 'Count'])
            
            if status_df.empty:
                plt.text(0.5, 0.5, 'No payment status data available', ha='center', va='center', fontsize=14, transform=plt.gca().transAxes)
                plt.gca().set_axis_off()
            else:
                # Create a pivot table suitable for grouped bar chart
                pivot_df = status_df.pivot_table(
                    index=['Fee Type', 'Batch Year'], 
                    columns='Status', 
                    values='Count', 
                    fill_value=0
                ).reset_index()
                
                # Ensure both status columns exist
                for status in ['Paid', 'Unpaid']:
                    if status not in pivot_df.columns:
                        pivot_df[status] = 0
                
                # Create unique batch-fee combinations for x-axis
                pivot_df['x_label'] = pivot_df['Fee Type'] + ' - ' + pivot_df['Batch Year']
                
                # Set up the figure
                fig, ax = plt.subplots(figsize=(14, 8))
                
                # Width of a bar 
                bar_width = 0.35
                
                # Set up positions for the bars
                x = np.arange(len(pivot_df))
                
                # Create the grouped bars
                paid_bars = ax.bar(x - bar_width/2, pivot_df['Paid'], bar_width, label='Paid', color='#28a745')
                unpaid_bars = ax.bar(x + bar_width/2, pivot_df['Unpaid'], bar_width, label='Unpaid', color='#dc3545')
                
                # Add data labels on bars
                def add_labels(bars):
                    for bar in bars:
                        height = bar.get_height()
                        if height > 0:  # Only add labels to bars with values
                            ax.text(bar.get_x() + bar.get_width()/2, height + 0.1,
                                    f'{int(height)}', ha='center', va='bottom',
                                    fontsize=9)
                
                add_labels(paid_bars)
                add_labels(unpaid_bars)
                
                # Add labels, title and legend
                ax.set_title('Fee Collection Status by Fee Type and Batch Year', fontsize=14, fontweight='bold')
                ax.set_ylabel('Number of Students', fontsize=12)
                ax.set_xticks(x)
                ax.set_xticklabels(pivot_df['x_label'], rotation=45, ha='right', fontsize=10)
                ax.legend()
                ax.grid(axis='y', linestyle='--', alpha=0.7)
                
                # Add a horizontal line at y=0
                ax.axhline(y=0, color='k', linestyle='-', alpha=0.3)
                
                # Adjust layout
                plt.tight_layout()
            
        except Exception as e:
            app.logger.error(f"Error generating payment status chart: {str(e)}")
            import traceback
            app.logger.error(traceback.format_exc())
            plt.text(0.5, 0.5, f'Error: {str(e)}', 
                    ha='center', va='center', fontsize=14, transform=plt.gca().transAxes)
            plt.gca().set_axis_off()

        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        buf.seek(0)
        charts['batch_completion'] = base64.b64encode(buf.read()).decode('utf-8')
        plt.close('all')  # Close all figures
        
        # Chart 3: MODIFIED - Daily Fee Collection (Total per day - single bar instead of by fee type)
        # Added more robust error handling for date processing
        plt.figure(figsize=(14, 8))  # Increased size for better visibility
        
        try:
            # Filter only paid records with payment dates - explicitly check for None and NaT values
            date_data = df[~pd.isna(df['payment_date'])].copy()
            
            if date_data.empty:
                app.logger.warning("No payment date data available for plotting daily chart")
                plt.text(0.5, 0.5, 'No payment date data available for the selected filters', 
                        ha='center', va='center', fontsize=14, transform=plt.gca().transAxes)
                plt.gca().set_axis_off()
            else:
                # Convert payment_date to datetime if it's not already - with explicit error handling
                if not pd.api.types.is_datetime64_any_dtype(date_data['payment_date']):
                    try:
                        app.logger.info(f"Converting payment_date column to datetime. Current dtype: {date_data['payment_date'].dtype}")
                        
                        # First attempt: Convert with pd.to_datetime with errors='coerce' to handle invalid dates
                        date_data['payment_date'] = pd.to_datetime(date_data['payment_date'], errors='coerce')
                        
                        # Drop rows where the conversion failed
                        date_data = date_data.dropna(subset=['payment_date'])
                        
                        if date_data.empty:
                            raise ValueError("All payment dates were invalid after conversion")
                            
                    except Exception as date_error:
                        app.logger.error(f"Error converting payment dates: {str(date_error)}")
                        plt.text(0.5, 0.5, f'Error processing payment dates: {str(date_error)}', 
                                ha='center', va='center', fontsize=14, transform=plt.gca().transAxes)
                        plt.gca().set_axis_off()
                        raise  # Re-raise to trigger the outer exception handler
                
                # Extract date for daily grouping - now with safer error handling
                try:
                    date_data['payment_day'] = date_data['payment_date'].dt.date
                    
                    # Apply date range filter if provided in filters
                    if filters and ('start_date' in filters or 'end_date' in filters):
                        try:
                            # If we have a start date filter
                            if 'start_date' in filters and filters['start_date']:
                                start_date = pd.to_datetime(filters['start_date']).date()
                                date_data = date_data[date_data['payment_day'] >= start_date]
                                
                            # If we have an end date filter
                            if 'end_date' in filters and filters['end_date']:
                                end_date = pd.to_datetime(filters['end_date']).date()
                                date_data = date_data[date_data['payment_day'] <= end_date]
                            
                            # Update chart title to reflect filtered date range
                            date_range_text = ""
                            if 'start_date' in filters and filters['start_date']:
                                date_range_text += f"From {filters['start_date']}"
                            if 'end_date' in filters and filters['end_date']:
                                date_range_text += f" To {filters['end_date']}"
                            
                            if date_range_text:
                                plt.title(f'Daily Fee Collection ({date_range_text})', fontsize=14, fontweight='bold')
                            else:
                                # Use the default title logic
                                if len(all_dates) > 10 and not ('start_date' in filters or 'end_date' in filters):
                                    plt.title('Daily Fee Collection (Last 10 Days)', fontsize=14, fontweight='bold')
                                else:
                                    plt.title('Daily Fee Collection', fontsize=14, fontweight='bold')
                        
                        except Exception as date_filter_error:
                            app.logger.error(f"Error applying date filters: {str(date_filter_error)}")
                            # Continue with unfiltered data if there's an error with the date filtering
                    
                    # Show only the last 10 days with data
                    all_dates = sorted(date_data['payment_day'].unique())
                    if len(all_dates) > 10:
                        # Keep only the 10 most recent days
                        selected_dates = all_dates[-10:]
                        date_data = date_data[date_data['payment_day'].isin(selected_dates)]
                        plt.title('Daily Fee Collection (Last 10 Days)', fontsize=14, fontweight='bold')
                    else:
                        plt.title('Daily Fee Collection', fontsize=14, fontweight='bold')
                    
                    # Group by day to get total payments (combine all fee types)
                    payments_per_day = date_data.groupby('payment_day')['paid_amount'].sum().reset_index()
                    
                    # Log the grouped data for debugging
                    app.logger.info(f"Daily payments data: {payments_per_day.head(10).to_dict()}")
                    
                    # Sort by date for chronological display
                    payments_per_day = payments_per_day.sort_values('payment_day')
                    
                    # Get dates and amounts for plotting
                    dates = payments_per_day['payment_day']
                    amounts = payments_per_day['paid_amount']
                    
                    # Format the x-axis date labels
                    date_labels = [d.strftime('%d %b') for d in dates]
                    
                    # Create the bar chart with a single bar per day
                    ax = plt.subplot(111)
                    bars = ax.bar(range(len(dates)), amounts, color='#4CAF50', width=0.6)
                    
                    # Add data labels on top of bars
                    for bar in bars:
                        height = bar.get_height()
                        if height > 0:
                            ax.text(bar.get_x() + bar.get_width()/2., height + 100,
                                 f'₹{int(height):,}', ha='center', va='bottom', 
                                 fontsize=10)
                    
                    # Setup axes and labels
                    plt.xlabel('Date', fontsize=12)
                    plt.ylabel('Total Amount Collected (₹)', fontsize=12)
                    
                    # Set x-ticks with date labels
                    plt.xticks(range(len(dates)), date_labels, rotation=45, ha='right')
                    
                    # Add grid lines for easier reading
                    plt.grid(axis='y', linestyle='--', alpha=0.6)
                    
                except Exception as grouping_error:
                    app.logger.error(f"Error grouping by date: {str(grouping_error)}")
                    import traceback
                    app.logger.error(traceback.format_exc())
                    plt.text(0.5, 0.5, f'Error grouping payment data: {str(grouping_error)}', 
                            ha='center', va='center', fontsize=14, transform=plt.gca().transAxes)
                    plt.gca().set_axis_off()
            
            plt.tight_layout()
            
        except Exception as chart_error:
            app.logger.error(f"Error creating daily fee collection chart: {str(chart_error)}")
            import traceback
            app.logger.error(traceback.format_exc())
            plt.text(0.5, 0.5, f'Error creating chart: {str(chart_error)}', 
                    ha='center', va='center', fontsize=14, transform=plt.gca().transAxes)
            plt.gca().set_axis_off()

        # Save the chart regardless of whether we successfully created it or showed an error message
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        buf.seek(0)
        charts['payments_over_time'] = base64.b64encode(buf.read()).decode('utf-8')
        plt.close('all')  # Close all figurez
        
        plt.figure(figsize=(14, 8))  # Increased size for better visibility
        
        # Get all batch years from the student table first
        all_batch_years_query = db.session.query(Student.batch_year).distinct().order_by(Student.batch_year)
        all_batch_years = [year[0] for year in all_batch_years_query.all()]
        
        # Improved query for payment totals by batch year and fee type
        # Use an explicit LEFT JOIN to ensure all batch years are included
        fee_totals_query = db.session.query(
            Student.batch_year,
            Payment.fee_type,
            db.func.sum(Payment.amount_paid).label('total_paid')
        ).join(
            Payment,
            Payment.regd_no == Student.regd_no
        ).group_by(
            Student.batch_year,
            Payment.fee_type
        ).all()
        
        app.logger.info(f"Fee totals query returned {len(fee_totals_query)} records")
        
        # Create a DataFrame with all batch years and standard fee types
        standard_fee_types = ['CRT', 'Phase 2', 'Phase 3']
        all_combinations = []
        
        for batch_year in all_batch_years:
            for fee_type in standard_fee_types:
                all_combinations.append({
                    'batch_year': batch_year,
                    'fee_type': fee_type,
                    'total_paid': 0.0  # Default to zero
                })
        
        base_df = pd.DataFrame(all_combinations)
        
        # Process query results and update the DataFrame
        for batch_year, fee_type, total_paid in fee_totals_query:
            # Get the standardized fee type label
            std_fee_type = get_standardized_fee_type_label(fee_type)
            
            # Only update if it's one of our standard fee types
            if std_fee_type in standard_fee_types:
                # Find the matching row in our DataFrame
                mask = (base_df['batch_year'] == batch_year) & (base_df['fee_type'] == std_fee_type)
                
                # Add the payment amount to the existing value (to handle multiple records with same std fee type)
                if any(mask):
                    base_df.loc[mask, 'total_paid'] += float(total_paid)
        
        # Create pivot table for plotting
        pivot_df = base_df.pivot(index='batch_year', columns='fee_type', values='total_paid')
        
        # Reset index to get batch_year as column and ensure proper ordering
        pivot_df = pivot_df.reindex(all_batch_years).reset_index()
        
        # Log the pivot table data for debugging
        app.logger.info(f"Pivot DataFrame columns: {pivot_df.columns.tolist()}")
        app.logger.info(f"Pivot DataFrame shape: {pivot_df.shape}")
        app.logger.info(f"Pivot DataFrame head: {pivot_df.head().to_dict()}")
        
        # Set up the figure with adjusted size
        fig, ax = plt.subplots(figsize=(14, 8))
        
        # Define bar properties
        bar_width = 0.25
        index = np.arange(len(pivot_df))
        
        # Make sure all required columns exist
        for fee_type in standard_fee_types:
            if fee_type not in pivot_df.columns:
                pivot_df[fee_type] = 0
        
        # Create grouped bars for each fee type with custom colors and error handling
        crt_bars = ax.bar(index - bar_width, pivot_df['CRT'], bar_width, label='CRT', color='#4CAF50')
        phase2_bars = ax.bar(index, pivot_df['Phase 2'], bar_width, label='Phase 2', color='#2196F3')
        phase3_bars = ax.bar(index + bar_width, pivot_df['Phase 3'], bar_width, label='Phase 3', color='#FFC107')
        
        # Add data labels to each bar
        def add_labels(bars):
            for bar in bars:
                height = bar.get_height()
                if height > 0:  # Only add labels to bars with values
                    ax.text(bar.get_x() + bar.get_width()/2., height + 500,
                            f'₹{int(height):,}', ha='center', va='bottom', 
                            rotation=0, fontsize=9)
        
        add_labels(crt_bars)
        add_labels(phase2_bars)
        add_labels(phase3_bars)
        
        # Set up axes, labels, and title
        ax.set_title('Fee Collection by Batch Year and Fee Type', fontsize=14, fontweight='bold')
        ax.set_xlabel('Batch Year', fontsize=12)
        ax.set_ylabel('Amount Collected (₹)', fontsize=12)
        ax.set_xticks(index)
        ax.set_xticklabels(pivot_df['batch_year'], rotation=45)
        ax.legend()
        ax.grid(axis='y', linestyle='--', alpha=0.7)
        
        plt.tight_layout()
        
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        buf.seek(0)
        charts['total_fee'] = base64.b64encode(buf.read()).decode('utf-8')
        plt.close('all') 

    except Exception as e:
        app.logger.error(f"Error creating visualizations: {str(e)}")
        import traceback
        app.logger.error(traceback.format_exc())
        charts["error"] = str(e) 
    return charts 

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    # Get date range parameters for daily fee chart
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    # Create filters dict including date range if provided
    filters = {}
    if start_date:
        filters['start_date'] = start_date
    if end_date:
        filters['end_date'] = end_date
    
    try:
        # Get summary statistics for dashboard cards
        crt_data = get_fee_type_summary('CRT')
        phase2_data = get_fee_type_summary('Phase 2')
        phase3_data = get_fee_type_summary('Phase 3')
        total_data = get_total_summary()
        
        # Get payment status counts for summary
        payment_status_counts = {
            'fully_paid': crt_data.get('fully_paid', 0) + phase2_data.get('fully_paid', 0) + phase3_data.get('fully_paid', 0),
            'total_students': crt_data.get('total_students', 0) + phase2_data.get('total_students', 0) + phase3_data.get('total_students', 0),
            'not_paid': crt_data.get('not_paid', 0) + phase2_data.get('not_paid', 0) + phase3_data.get('not_paid', 0)
        }
        
        app.logger.info(f"CRT data: {crt_data}")
        app.logger.info(f"Phase 2 data: {phase2_data}")
        app.logger.info(f"Phase 3 data: {phase3_data}")
        
        # Create visualizations with date filters
        try:
            charts = create_visualizations(filters)
            app.logger.info(f"Charts generated: {list(charts.keys())}")
        except Exception as chart_error:
            app.logger.error(f"Error creating charts: {str(chart_error)}")
            import traceback
            app.logger.error(traceback.format_exc())
            charts = {"error": f"Failed to generate charts: {str(chart_error)}"}
        
        # Get filter options from database (still needed for other parts)
        batch_years = [row[0] for row in db.session.query(Student.batch_year).distinct().all()]
        return render_template('dashboard.html', 
                            crt_data=crt_data, 
                            phase2_data=phase2_data, 
                            phase3_data=phase3_data, 
                            total_data=total_data, 
                            payment_status_counts=payment_status_counts, 
                            charts=charts,
                            batch_years=batch_years,
                            )
    except Exception as e:
        app.logger.error(f"Error loading dashboard: {str(e)}")
        import traceback
        app.logger.error(traceback.format_exc())
        flash(f"Error loading dashboard: {str(e)}", "error")
        return render_template('dashboard.html', error=str(e))

def get_fee_type_summary(fee_type):
    """Get summary statistics for a specific fee type"""
    try:
        normalized_type = normalize_fee_type(fee_type)
        
        # Get all students with this fee type and their fee amounts
        fee_entries = db.session.query(
            FeeMaster.regd_no, 
            FeeMaster.amount
        ).filter(
            db.func.lower(FeeMaster.fee_type).like(f'%{normalized_type}%')
        ).all()
        
        # Get total students who should pay this fee
        total_students = len(fee_entries)
        total_fee_amount = sum(float(amount) for _, amount in fee_entries)
        
        # Create a mapping of registration numbers to fee amounts
        student_fees = {regd_no: float(amount) for regd_no, amount in fee_entries}
        
        # Get all payment records for this fee type
        payments = db.session.query(
            Payment.regd_no,
            db.func.sum(Payment.amount_paid).label('total_paid')
        ).filter(
            db.func.lower(Payment.fee_type).like(f'%{normalized_type}%')
        ).group_by(
            Payment.regd_no
        ).all()
        
        # Create a mapping of registration numbers to paid amounts
        student_payments = {regd_no: float(total_paid) for regd_no, total_paid in payments}
        
        # Calculate total collected amount
        total_collected = sum(student_payments.values())
        
        # Count fully paid, partially paid, and not paid students
        fully_paid_count = 0
        partially_paid_count = 0
        not_paid_count = 0
        
        # Check payment status for each student
        for regd_no, fee_amount in student_fees.items():
            paid_amount = student_payments.get(regd_no, 0)
            
            # Use small epsilon to avoid floating point precision issues
            epsilon = 0.01
            if paid_amount >= (fee_amount - epsilon):
                fully_paid_count += 1
            elif paid_amount > 0:
                partially_paid_count += 1
            else:
                not_paid_count += 1
        
        return {
            'total': total_collected,         # Total amount collected so far
            'target_amount': total_fee_amount, # Total expected if everyone pays
            'count': len(payments),           # Number of students who made any payment
            'total_students': total_students, # Total students who should pay
            'fully_paid': fully_paid_count,
            'partially_paid': partially_paid_count,
            'not_paid': not_paid_count
        }
    except Exception as e:
        app.logger.error(f"Error getting summary for {fee_type}: {str(e)}")
        import traceback
        app.logger.error(traceback.format_exc())            
        return {'total': 0, 'count': 0, 'total_students': 0, 'fully_paid': 0}

def get_total_summary():
    """Get total collection summary across all fee types"""
    try:
        # Get total amount collected
        total_amount = db.session.query(
            db.func.sum(Payment.amount_paid)
        ).scalar() or 0
        
        # Get total count of unique students who paid
        student_count = db.session.query(
            db.func.count(db.distinct(Payment.regd_no))
        ).scalar() or 0
        
        return {'total': total_amount, 'count': student_count}
    except Exception as e:
        app.logger.error(f"Error getting total summary: {str(e)}")
        return {'total': 0, 'count': 0}

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        # Debugging - log request information
        app.logger.info(f"Request files: {request.files}")
        app.logger.info(f"Request form: {request.form}")
        
        # Check if file was uploaded
        if 'excel-file' not in request.files:
            flash('No file part found in the request. Please ensure you selected a file.', 'error')
            return redirect(request.url)
        
        file = request.files['excel-file']
        app.logger.info(f"File received: {file.filename}, Content type: {file.content_type}")
        
        # If user submits an empty form
        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(request.url)
        
        # Check file extension
        allowed_extensions = ['.xlsx', '.xls', '.csv']
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext not in allowed_extensions:
            flash(f'Invalid file format. Please upload an Excel file (.xlsx, .xls) or CSV file. Got: {file_ext}', 'error')
            return redirect(request.url)
        
        try:
            # Create a temporary file to save the uploaded content
            temp_path = os.path.join(os.path.dirname(__file__), 'temp_upload' + file_ext)
            file.save(temp_path)
            app.logger.info(f"Saved file temporarily to {temp_path}")
            
            # Read the file based on extension
            if file_ext == '.csv':
                df = pd.read_csv(temp_path)
            else:
                df = pd.read_excel(temp_path)
            
            # Remove the temporary file
            os.remove(temp_path)
            
            # Log dataframe info
            app.logger.info(f"DataFrame loaded with shape: {df.shape}")
            app.logger.info(f"DataFrame columns: {df.columns.tolist()}")
            
            # Fix column names - convert all to lowercase and strip spaces
            df.columns = [col.lower().strip() for col in df.columns]
            
            # Map common column name variations to our standard
            column_mapping = {
                'registration_number': 'regd_no',
                'registration': 'regd_no',
                'reg_no': 'regd_no',
                'regno': 'regd_no',
                'student_name': 'name',
                'batch': 'batch_year',
                'department': 'branch',
                'phone': 'mobile',
                'contact': 'mobile',
                'fee': 'amount',
                'fee_amount': 'amount'
            }
            
            # Apply column mapping
            for old_col, new_col in column_mapping.items():
                if old_col in df.columns and new_col not in df.columns:
                    df[new_col] = df[old_col]
            
            # Check required columns
            required_columns = ['batch_year', 'regd_no', 'name', 'branch', 'mobile', 'fee_type', 'amount']
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                flash(f"Missing required columns: {', '.join(missing_columns)}", 'error')
                flash(f"Available columns: {', '.join(df.columns)}", 'error')
                return redirect(request.url)
            
            # Process data
            records_processed = 0
            errors = []
            added_reg_numbers = set()
            
            # Start a transaction for better performance
            try:
                # Process each row
                for index, row in df.iterrows():
                    try:
                        # Clean and normalize input data
                        registration_number = str(row['regd_no']).strip()
                        name = str(row['name']).strip()
                        batch_year = str(row['batch_year']).strip()
                        branch = str(row['branch']).strip()
                        mobile = str(row['mobile']).strip() if not pd.isna(row['mobile']) else ""
                        
                        # Normalize fee_type and handle comma-separated types
                        fee_type_raw = str(row['fee_type']).strip()
                        fee_types = [normalize_fee_type(ft.strip()) for ft in fee_type_raw.split(',')]
                        
                        # Convert amount to float with validation
                        try:
                            total_fee_amount = float(row['amount'])
                            if total_fee_amount <= 0:
                                raise ValueError("Fee amount must be positive")
                            
                            # If multiple fee types, amounts will be proportionally assigned later
                            # Just store the total for now
                            fee_amount_per_type = total_fee_amount
                                
                        except (ValueError, TypeError) as e:
                            raise ValueError(f"Invalid fee amount: {row['amount']} - {str(e)}")
                        
                        # Optional fields
                        batch = str(row.get('batch', '')).strip() if 'batch' in row else ""
                        remarks = str(row.get('remarks', '')).strip() if 'remarks' in row else ""
                        
                        # Check if student exists
                        student = Student.query.filter_by(regd_no=registration_number).first()
                        
                        # If student doesn't exist, create a new one
                        if not student:
                            student = Student(
                                regd_no=registration_number,
                                name=name,
                                batch_year=batch_year,
                                branch=branch,
                                mobile=mobile
                            )
                            db.session.add(student)
                            db.session.flush()  # Flush to get the student.id
                            app.logger.info(f"Added new student: {registration_number} - {name}")
                        else:
                            # Update existing student information
                            student.name = name
                            student.batch_year = batch_year
                            student.branch = branch
                            student.mobile = mobile
                            app.logger.info(f"Updated existing student: {registration_number}")
                        
                        # Process each fee type separately
                        if len(fee_types) > 1:
                            # For multiple fee types, divide the amount proportionally
                            # Default to equal distribution
                            fee_amount_per_type = round(total_fee_amount / len(fee_types), 2)
                        
                        for fee_type in fee_types:
                            # Check if fee entry exists for this student and fee type
                            fee_entry = FeeMaster.query.filter_by(
                                regd_no=registration_number,
                                fee_type=fee_type
                            ).first()
                            
                            # If fee entry doesn't exist, create a new one
                            if not fee_entry:
                                fee_entry = FeeMaster(
                                    regd_no=registration_number,
                                    fee_type=fee_type,
                                    amount=fee_amount_per_type,
                                    remarks=f"{remarks} (Part of: {fee_type_raw})" if len(fee_types) > 1 else remarks
                                )
                                db.session.add(fee_entry)
                                app.logger.info(f"Added new fee entry for {registration_number}: {fee_type} - ₹{fee_amount_per_type}")
                            else:
                                # Update existing fee entry
                                fee_entry.amount = fee_amount_per_type
                                fee_entry.remarks = remarks
                                app.logger.info(f"Updated existing fee entry for {registration_number}: {fee_type}")
                            
                            # Ensure payment information is included (optional columns)
                            if 'paid_amount' in df.columns and 'payment_date' in df.columns and pd.notna(row['paid_amount']):
                                # Parse payment amount
                                paid_amount = float(row['paid_amount'])
                                
                                # Calculate proportional payment based on fee amounts
                                proportion = fee_amount_per_type / total_fee_amount
                                paid_amount = round(paid_amount * proportion, 2)
                                
                                if paid_amount > 0 and pd.notna(row['payment_date']):
                                    # Parse payment date
                                    payment_date = row['payment_date']
                                    if isinstance(payment_date, str):
                                        payment_date = datetime.strptime(payment_date, '%Y-%m-%d').date()
                                    elif isinstance(payment_date, (pd.Timestamp, datetime)):
                                        payment_date = payment_date.date()
                                    
                                    received_by = str(row.get('received_by', 'Excel Import')).strip() if 'received_by' in row else 'Excel Import'
                                    
                                    # Create payment record
                                    payment = Payment(
                                        batch_year=batch_year,
                                        regd_no=registration_number,
                                        fee_type=fee_type,
                                        amount_paid=paid_amount,
                                        date=payment_date,
                                        received_by=received_by
                                    )
                                    db.session.add(payment)
                                    app.logger.info(f"Added payment record for {registration_number}: {fee_type} - ₹{paid_amount}")
                    
                        # Add to the set
                        added_reg_numbers.add(registration_number)
                        records_processed += 1
                    
                    except Exception as e:
                        errors.append(f"Warning on row {index + 2}: {str(e)}")
                        app.logger.warning(f"Warning on row {index + 2}: {str(e)}")
                
                # Commit the transaction
                db.session.commit()
            
            except Exception as e:
                db.session.rollback()
                app.logger.error(f"Error processing data: {str(e)}")
                flash(f"Error processing data: {str(e)}", 'error')
                return redirect(request.url)
            
            # Show success message
            flash(f"Successfully processed {records_processed} records!", 'success')
            if errors:
                for error in errors[:10]:  # Limit to first 10 errors
                    flash(error, 'warning')
            
            # Redirect to student details page to see the added registration numbers
            return redirect(url_for('student_details', reg_numbers=list(added_reg_numbers)))
        
        except Exception as e:
            app.logger.error(f"Error processing file: {str(e)}")
            flash(f"Error processing file: {str(e)}", 'error')
            return redirect(request.url)
    
    return render_template('upload.html')

@app.route('/payments', methods=['GET', 'POST'])
def payments():
    # Initialize empty payment history in case of errors
    payment_history = []
    
    # Get query parameters to pre-populate the form if coming from student details
    regd_no = request.args.get('regd_no', '')
    fee_type = request.args.get('fee_type', '')
    
    # Normalize fee type if provided
    if fee_type:
        fee_type = normalize_fee_type(fee_type)
    
    # If pre-populated, try to get student details
    student_name = ''
    batch_year = ''
    if regd_no:
        try:
            student = Student.query.filter_by(regd_no=regd_no).first()
            if student:
                student_name = student.name
                batch_year = student.batch_year
        except Exception as e:
            app.logger.error(f"Error fetching student for pre-population: {str(e)}")
    
    try:
        # Get recent payment history - use simpler query and handle null values
        recent_payments = db.session.query(
            Payment.id,
            Payment.regd_no, 
            Student.name,
            Payment.batch_year, 
            Payment.fee_type, 
            Payment.amount_paid, 
            Payment.date,
            Payment.received_by
        ).outerjoin(  # Changed to outer join to avoid failures if student record is missing
            Student, 
            Payment.regd_no == Student.regd_no
        ).order_by(
            Payment.date.desc()
        ).limit(10).all()
        
        # Format the payment history with safer null handling
        for payment in recent_payments:
            payment_history.append({
                'regd_no': payment.regd_no,
                'student_name': payment.name if payment.name else "Unknown",
                'batch_year': payment.batch_year,
                'fee_type': get_standardized_fee_type_label(payment.fee_type),
                'amount': payment.amount_paid,
                'date': payment.date.strftime('%Y-%m-%d') if payment.date else "Unknown",
                'received_by': payment.received_by,
                'status': 'Paid'
            })
    except Exception as e:
        # Log the error and use empty payment history
        import traceback
        app.logger.error(f"Error retrieving payment history: {str(e)}")
        app.logger.error(traceback.format_exc())
    
    if request.method == 'POST':
        try:
            regd_number = request.form.get('regd-number')
            batch_year = request.form.get('batch-year')
            fee_type_combined = request.form.get('fee-type-combined', '')
            payment_method = request.form.get('payment-method')
            amount = request.form.get('payment-amount')
            payment_date = request.form.get('payment-date')
            received_by = request.form.get('received-by')
            
            # Split combined fee types by pipe symbol
            fee_types = fee_type_combined.split('|') if fee_type_combined else []
            
            # Normalize each fee type to ensure consistency
            normalized_fee_types = []
            for fee_type in fee_types:
                fee_type = fee_type.strip()
                if not fee_type:
                    continue
                    
                # Apply more consistent normalization
                normalized_type = normalize_fee_type(fee_type)
                normalized_fee_types.append(normalized_type)
            
            fee_types = normalized_fee_types
            
            # Validate required fields
            if not all([regd_number, batch_year, fee_types, payment_method, amount, payment_date, received_by]):
                flash('All fields are required.', 'error')
                return render_template('payments.html', 
                                       payment_history=payment_history,
                                       reg_no=regd_no,
                                       fee_type=fee_type,
                                       student_name=student_name,
                                       batch_year=batch_year)
            
            # Simple validation for student existence
            student = Student.query.filter_by(regd_no=regd_number).first()
            student_name = student.name if student else "Unknown"
            
            # Convert amount to float for distribution
            total_amount = float(amount)
            
            # If multiple fee types, distribute amount proportionally based on fee amounts rather than remaining dues
            if len(fee_types) > 1:
                # Get fee info to calculate proportional distribution
                student_fees = []
                total_fee_amount = 0
                
                for fee_type in fee_types:
                    # Get fee master entry - use more flexible matching with LIKE
                    fee_entry = db.session.query(FeeMaster).filter(
                        FeeMaster.regd_no == regd_number,
                        db.func.lower(FeeMaster.fee_type).like(f'%{fee_type.lower()}%')
                    ).first()
                    
                    if fee_entry:
                        # Get the fee amount
                        fee_amount = float(fee_entry.amount)
                        total_fee_amount += fee_amount
                        
                        # Get payments for this fee type
                        payment = db.session.query(
                            db.func.sum(Payment.amount_paid).label('total_paid')
                        ).filter(
                            db.func.lower(FeeMaster.fee_type).like(f'%{fee_type.lower()}%')
                        ).first()
                        
                        # Get existing payments for this fee type
                        paid_amount = payment.total_paid if payment and payment.total_paid else 0
                        
                        student_fees.append({
                            'fee_type': fee_entry.fee_type,
                            'amount': fee_amount,
                            'paid': paid_amount,
                            'remaining': max(0, fee_amount - paid_amount)
                        })
                
                # If no fee types found, show error
                if not student_fees:
                    flash('No matching fee types found for this student.', 'error')
                    return render_template('payments.html', 
                                       payment_history=payment_history,
                                       reg_no=regd_no,
                                       fee_type=fee_type,
                                       student_name=student_name,
                                       batch_year=batch_year)
                
                # Calculate distribution based on original fee amounts (not remaining amounts)
                total_paid_so_far = 0
                for i, fee in enumerate(student_fees):
                    if total_fee_amount > 0:
                        # Calculate proportional amount based on original fee amount
                        proportion = fee['amount'] / total_fee_amount
                        fee_payment = round(total_amount * proportion, 2)
                    else:
                        # Equal distribution if couldn't determine proportion (shouldn't happen normally)
                        fee_payment = round(total_amount / len(student_fees), 2)
                    
                    # Avoid rounding errors by assigning remaining amount to last fee
                    if i == len(student_fees) - 1:
                        fee_payment = round(total_amount - total_paid_so_far, 2)
                    
                    total_paid_so_far += fee_payment
                    fee['paid_amount'] = fee_payment
                    
                    # Convert date string to Python date object
                    try:
                        payment_date_obj = datetime.strptime(payment_date, '%Y-%m-%d').date()
                    except ValueError:
                        flash('Invalid date format. Please use YYYY-MM-DD format.', 'error')
                        return render_template('payments.html', 
                                           payment_history=payment_history,
                                           reg_no=regd_no,
                                           fee_type=fee_type,
                                           student_name=student_name,
                                           batch_year=batch_year)
                    
                    # Create payment record for this fee type
                    payment = Payment(
                        batch_year=batch_year,
                        regd_no=regd_number,
                        fee_type=fee['fee_type'],
                        amount_paid=fee['paid_amount'],
                        date=payment_date_obj,
                        received_by=received_by
                    )
                    
                    db.session.add(payment)
            else:
                # Single fee type - use the traditional flow
                fee_type = fee_types[0] if fee_types else ""
                
                # Find exact fee type from database using case-insensitive LIKE
                db_fee_entry = db.session.query(FeeMaster.fee_type).filter(
                    FeeMaster.regd_no == regd_number,
                    db.func.lower(FeeMaster.fee_type).like(f'%{fee_type.lower()}%')
                ).first()
                
                # Use the exact fee type string from database if found
                if db_fee_entry:
                    fee_type = db_fee_entry.fee_type
                
                # Convert date string to Python date object
                try:
                    payment_date_obj = datetime.strptime(payment_date, '%Y-%m-%d').date()
                except ValueError:
                    flash('Invalid date format. Please use YYYY-MM-DD format.', 'error')
                    return render_template('payments.html', 
                                       payment_history=payment_history,
                                       reg_no=regd_no,
                                       fee_type=fee_type,
                                       student_name=student_name,
                                       batch_year=batch_year)
                
                # Create and add payment record
                payment = Payment(
                    batch_year=batch_year,
                    regd_no=regd_number,
                    fee_type=fee_type,
                    amount_paid=float(amount),
                    date=payment_date_obj,
                    received_by=received_by
                )
                db.session.add(payment)
            
            # Save to database with proper transaction handling
            try:
                db.session.commit()
                
                # Show the number of fee types paid for in the flash message
                if len(fee_types) > 1:
                    flash(f'Payment for {len(fee_types)} fee types processed successfully!', 'success')
                else:
                    flash('Payment processed successfully!', 'success')
                
                # Redirect to student details page to see updated payment status
                return redirect(url_for('student_details', reg_numbers=[regd_number], display_type='payment_details'))
                
            except Exception as e:
                db.session.rollback()
                app.logger.error(f"Error saving payment: {str(e)}")
                flash(f'Error saving payment: {str(e)}', 'error')
            
        except Exception as e:
            import traceback
            app.logger.error(f"Error processing payment: {str(e)}")
            app.logger.error(traceback.format_exc())
            flash(f'Error processing payment: {str(e)}', 'error')
    
    # Always return the template, even after errors
    return render_template('payments.html', 
                          payment_history=payment_history, 
                          reg_no=regd_no,
                          fee_type=fee_type,
                          student_name=student_name,
                          batch_year=batch_year)

@app.route('/download_template')

def download_template():
    """Generate and provide a sample template for download in Excel or CSV format"""
    format_type = request.args.get('format', 'excel')  # Default to Excel if not specified
    
    try:
        # Create a DataFrame with sample data
        sample_data = {
            'batch_year': ['2022-2026', '2021-2025', '2022-2026'],
            'regd_no': ['REG2022001', 'REG2021002', 'REG2022003'],
            'name': ['John Doe', 'Jane Smith', 'Alice Johnson'],
            'branch': ['CSE', 'IT', 'CSE-AI&DS'],
            'mobile': ['9876543210', '8765432109', '7654321098'],
            'fee_type': ['CRT', 'Phase 2', 'Phase 3'],
            'amount': [10000, 15000, 20000],
        }
        
        df = pd.DataFrame(sample_data)
        
        if format_type.lower() == 'csv':
            # Create CSV in memory
            output = BytesIO()
            df.to_csv(output, index=False, encoding='utf-8')
            output.seek(0)
            
            # Return CSV file
            return send_file(
                output,
                mimetype='text/csv',
                download_name='student_fee_template.csv',
                as_attachment=True
            )
        else:
            # Create Excel file (default option)
            output = BytesIO()
            
            # Use xlsxwriter as the engine
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df.to_excel(writer, sheet_name='Student Data', index=False)
                
                # Get the xlsxwriter workbook and worksheet objects
                workbook = writer.book
                worksheet = writer.sheets['Student Data']
                
                # Add some formatting
                header_format = workbook.add_format({
                    'bold': True,
                    'text_wrap': True,
                    'valign': 'top',
                    'fg_color': '#D7E4BC',
                    'border': 1
                })
                
                # Write the column headers with the defined format
                for col_num, value in enumerate(df.columns.values):
                    worksheet.write(0, col_num, value, header_format)
                    
                # Set column widths
                worksheet.set_column('A:I', 15)  # Updated to include remarks column
                
                # Add a note about the template usage
                worksheet.write(4, 0, 'Note: For Smart Interviews fee types, please specify a batch in the batch column.')
                worksheet.write(5, 0, 'The remarks column is optional and can be used to provide additional information.')
            
            # Reset file pointer to beginning
            output.seek(0)
            
            # Send file with a clear filename and mimetype
            return send_file(
                output,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                download_name='student_fee_template.xlsx'
            )
    
    except Exception as e:
        # Log the error for debugging
        app.logger.error(f"Error generating template: {str(e)}")
        flash(f"Could not generate template file: {str(e)}", "error")
        return redirect(url_for('upload'))

@app.route('/student_details', methods=['GET'])
@login_required
def student_details():
    # Initialize empty lists/values in case of database errors
    batch_years = []
    fee_types = []
    branches = []
    student_records = []
    
    # Get filter parameters
    batch_year = request.args.get('batch_year')
    fee_type = request.args.get('fee_type')
    regd_no = request.args.get('regd_no', '')  # Add this line to get registration number filter
    reg_numbers = request.args.getlist('reg_numbers')
    payment_status = request.args.get('payment_status')
    student_name = request.args.get('student_name', '')
    branch = request.args.get('branch', '')
    
    # New parameter to toggle between student and payment details
    display_type = request.args.get('display_type', 'payment_details')  # Default to payment details
    
    # Add logging to track the request
    app.logger.info(f"Student details request - batch_year: {batch_year}, fee_type: {fee_type}, "
                    f"regd_no: {regd_no}, reg_numbers: {reg_numbers}, payment_status: {payment_status}, "
                    f"student_name: {student_name}, branch: {branch}, display_type: {display_type}")
    
    try:
        # Get batch years - use a simpler query with error handling
        batch_years_query = db.session.query(Student.batch_year).distinct()
        batch_years = [year[0] for year in batch_years_query.all()]
        if not batch_years:  # Fallback if no data
            batch_years = ['2020-2024', '2021-2025', '2022-2026', '2023-2027', '2024-2028', '2025-2029', '2026-2030']
    except Exception as e:
        app.logger.error(f"Error fetching batch years: {str(e)}")
        batch_years = ['2020-2024', '2021-2025', '2022-2026', '2023-2027', '2024-2028', '2025-2029', '2026-2030']  # Fallback
    
    try:
        # Get fee types - simplified query
        fee_types = [row[0] for row in db.session.query(FeeMaster.fee_type).distinct().all()]
        if not fee_types:  # Fallback if no data
            fee_types = ['CRT', 'Phase 2', 'Phase 3']
    except Exception as e:
        app.logger.error(f"Error fetching fee types: {str(e)}")
        fee_types = ['CRT', 'Phase 2', 'Phase 3']  # Fallback
    
    # Fixed branch list - always include these whether DB has data or not
    default_branches = ['CSE', 'IT', 'CSE-AI&DS', 'CSE-AI&ML', 'ECE', 'EEE', 'CE', 'ME']
    
    try:
        # Get branches from DB
        db_branches = [row[0] for row in db.session.query(Student.branch).distinct().all()]
        
        # Combine DB branches with default branches, removing duplicates
        branches = list(set(db_branches + default_branches))
        branches.sort()  # Sort alphabetically
    except Exception as e:
        app.logger.error(f"Error fetching branches: {str(e)}")
        branches = default_branches  # Fallback to default branches
    
    # Normalize fee type if provided
    if fee_type:
        fee_type = normalize_fee_type(fee_type)
    
    # Always query for records regardless of filters
    try:
        if display_type == 'student_details':
            # Query for student details only
            query = db.session.query(
                Student.batch_year,
                Student.regd_no,
                Student.name,
                Student.branch,
                Student.mobile
            )
            
            # Apply filters only if they are specified
            if batch_year:
                query = query.filter(Student.batch_year == batch_year)
            if regd_no:  # Add this filter for registration number
                query = query.filter(Student.regd_no.like(f'%{regd_no}%'))
            if reg_numbers:
                query = query.filter(Student.regd_no.in_(reg_numbers))
            if student_name:
                query = query.filter(Student.name.like(f'%{student_name}%'))
            if branch:
                query = query.filter(Student.branch == branch)
            
            # Execute the query to get the student data
            results = query.all()
            
            # Process the student results
            for row in results:
                student_records.append({
                    'batch_year': row.batch_year,
                    'registration_number': row.regd_no,
                    'name': row.name,
                    'branch': row.branch,
                    'mobile': row.mobile,
                    'display_type': 'student_details'
                })
            
        else:  # Default to payment_details
            # Modified approach: Group by student first, then collect fee type information
            # First, get all students matching our filters
            student_query = db.session.query(
                Student.batch_year,
                Student.regd_no,
                Student.name,
                Student.branch
            ).distinct()
            
            # Apply student-level filters
            if batch_year:
                student_query = student_query.filter(Student.batch_year == batch_year)
            if regd_no:  # Add this filter for registration number
                student_query = student_query.filter(Student.regd_no.like(f'%{regd_no}%'))
            if reg_numbers:
                student_query = student_query.filter(Student.regd_no.in_(reg_numbers))
            if student_name:
                student_query = student_query.filter(Student.name.like(f'%{student_name}%'))
            if branch:
                student_query = student_query.filter(Student.branch == branch)
                
            # Get all students matching our criteria
            students = student_query.all()
            
            # For each student, get their fee and payment information
            for student in students:
                try:
                    # Dictionary to hold fee type information for this student - using safer string keys
                    fee_info = {
                        'CRT': {'amount': 0, 'paid': 0, 'status': 'Not Paid', 'remarks': ''},
                        'Phase 2': {'amount': 0, 'paid': 0, 'status': 'Not Paid', 'remarks': ''},
                        'Phase 3': {'amount': 0, 'paid': 0, 'status': 'Not Paid', 'remarks': ''}
                    }
                    
                    # Debug log
                    app.logger.debug(f"Processing student: {student.regd_no}, Initial fee_info: {fee_info}")
                    
                    # Get all fee types for this student from FeeMaster
                    fee_entries = db.session.query(
                        FeeMaster.fee_type,
                        FeeMaster.amount,
                        FeeMaster.remarks
                    ).filter(FeeMaster.regd_no == student.regd_no).all()
                    
                    # Skip student if they have no fee entries matching filter
                    if fee_type and not any(fee_type.lower() in entry.fee_type.lower() for entry in fee_entries):
                        continue
                    
                    # Process each fee entry
                    for entry in fee_entries:
                        std_fee_type = get_standardized_fee_type_label(entry.fee_type)
                        
                        # Skip if we're filtering by fee type and this doesn't match
                        if fee_type and fee_type.lower() not in entry.fee_type.lower():
                            continue
                            
                        if std_fee_type in fee_info:
                            fee_info[std_fee_type]['amount'] = entry.amount
                            fee_info[std_fee_type]['remarks'] = entry.remarks or ''
                            
                            # IMPROVED PAYMENT QUERY: Use normalized fee type matching for reliable results
                            # First, get all possible normalized versions of this fee type
                            normalized_entry_type = normalize_fee_type(entry.fee_type)
                            fee_type_label = get_standardized_fee_type_label(entry.fee_type)
                            
                            app.logger.debug(f"FEE TYPE DEBUG - Original: '{entry.fee_type}', Normalized: '{normalized_entry_type}', Label: '{fee_type_label}'")
                            
                            # Get payment info for this fee type with more flexible matching
                            payment = db.session.query(
                                db.func.sum(Payment.amount_paid).label('total_paid')
                            ).filter(
                                Payment.regd_no == student.regd_no,
                                # Use a broader matching approach with LIKE to match fee types
                                db.func.lower(Payment.fee_type).like(f'%{normalized_entry_type}%')
                            ).first()
                            
                            # More robust handling of the payment value
                            total_paid = payment.total_paid if payment and payment.total_paid else 0
                            fee_info[std_fee_type]['paid'] = total_paid
                            
                            # Enhanced debug logging
                            app.logger.debug(f"Student: {student.regd_no}, Fee Type: {std_fee_type}")
                            app.logger.debug(f"Amount (type {type(entry.amount)}): {entry.amount}")
                            app.logger.debug(f"Total Paid (type {type(total_paid)}): {total_paid}")
                            
                            # Convert values to float with proper error handling
                            try:
                                entry_amount = float(entry.amount)
                                paid_amount = float(total_paid)
                                
                                # Avoid floating point precision issues with a small epsilon
                                if paid_amount >= (entry_amount - 0.01):
                                    fee_info[std_fee_type]['status'] = 'Paid'
                                    app.logger.debug(f"✅ Status set to PAID: {paid_amount} >= {entry_amount}")
                                elif paid_amount > 0:
                                    fee_info[std_fee_type]['status'] = 'Partially Paid'
                                    app.logger.debug(f"⚠️ Status set to PARTIALLY PAID: 0 < {paid_amount} < {entry_amount}")
                                else:
                                    fee_info[std_fee_type]['status'] = 'Not Paid'
                                    app.logger.debug(f"❌ Status set to NOT PAID: {paid_amount} <= 0")
                            except (ValueError, TypeError) as e:
                                app.logger.error(f"Error converting payment values to float: {str(e)}")
                                # Default to Not Paid on conversion error, but log it
                                fee_info[std_fee_type]['status'] = 'Not Paid'
                    
                    # Only include students with at least one fee type that's not zero amount
                    has_fee_data = any(info['amount'] > 0 for info in fee_info.values())
                    if not has_fee_data:
                        continue
                    
                    # Add student record with fee information for all types
                    student_records.append({
                        'batch_year': student.batch_year,
                        'registration_number': student.regd_no,
                        'name': student.name,
                        'branch': student.branch,
                        'fee_info': fee_info,
                        'display_type': 'payment_details'
                    })
                    
                except Exception as student_error:
                    app.logger.error(f"Error processing student {student.regd_no}: {str(student_error)}")
                    import traceback
                    app.logger.error(traceback.format_exc())
            
            # Apply payment status filter if specified
            if payment_status:
                filtered_records = []
                for record in student_records:
                    if payment_status == 'fully_paid' and any(info['status'] == 'Paid' for info in record['fee_info'].values()):
                        filtered_records.append(record)
                    elif payment_status == 'partially_paid' and any(info['status'] == 'Partially Paid' for info in record['fee_info'].values()):
                        filtered_records.append(record)
                    elif payment_status == 'not_paid' and any(info['status'] == 'Not Paid' for info in record['fee_info'].values()):
                        filtered_records.append(record)
                student_records = filtered_records
        
        # Ensure we have valid data before rendering template
        if not student_records:
            flash("No student records found matching your criteria", "warning")
        
        # Debug final output
        app.logger.debug(f"Final student_records count: {len(student_records)}")
        
        # Render the template with all data
        return render_template('student_details.html', 
                              batch_years=batch_years, 
                              fee_types=fee_types, 
                              branches=branches, 
                              student_records=student_records, 
                              selected_batch_year=batch_year, 
                              selected_fee_type=fee_type,
                              selected_regd_no=regd_no,  # Pass registration number to template 
                              selected_payment_status=payment_status, 
                              selected_student_name=student_name, 
                              selected_branch=branch, 
                              selected_display_type=display_type,  # Add the new parameter
                              highlight_new=len(reg_numbers) > 0)  # Highlight if we came from upload with specific reg_numbers
    
    except Exception as e:
        app.logger.error(f"Error retrieving student details: {str(e)}")
        import traceback
        app.logger.error(traceback.format_exc())
        flash(f"Error retrieving student details: {str(e)}", "error")
        return render_template('student_details.html', error=str(e))

@app.route('/unpaid_students', methods=['GET'])
def unpaid_students():
    """Retrieve students with unpaid fees based on filters"""
    # Get filter parameters
    batch_year = request.args.get('batch_year')
    fee_type = request.args.get('fee_type')
    branch = request.args.get('branch')
    
    # Default to showing only students with no payments (not_paid)
    payment_status = request.args.get('payment_status', 'not_paid')
    
    # Check if user requested downloads
    download_excel = request.args.get('download_excel') == 'true'
    download_csv = request.args.get('download_csv') == 'true'
    
    try:
        # Get filter options
        batch_years = [year[0] for year in db.session.query(Student.batch_year).distinct().order_by(Student.batch_year).all()]
        fee_types = ['CRT', 'Phase 2', 'Phase 3']  # Standard fee types
        branches = [branch[0] for branch in db.session.query(Student.branch).distinct().order_by(Student.branch).all()]
        
        # Normalize fee type if provided
        normalized_fee_type = normalize_fee_type(fee_type) if fee_type else None
        
        # Base query to get all students
        student_query = db.session.query(
            Student.regd_no,
            Student.name,
            Student.batch_year,
            Student.branch,
            Student.mobile
        )
        
        # Apply filters to student query
        if batch_year:
            student_query = student_query.filter(Student.batch_year == batch_year)
        if branch:
            student_query = student_query.filter(Student.branch == branch)
        
        students = student_query.all()
        
        # Key is student registration number + standardized fee type, value is the student record
        unpaid_students_dict = {}
    
        for student in students:
            # Track which standardized fee types have been processed for this student
            processed_std_fee_types = set()
            
            # Determine which fee types to check
            fee_types_to_check = [normalize_fee_type(fee_type)] if fee_type else [normalize_fee_type(ft) for ft in fee_types]
            
            for normalized_type in fee_types_to_check:
                # Safely retrieve fee entries for this normalized type
                fee_entries = db.session.query(
                    FeeMaster.fee_type,
                    FeeMaster.amount,
                    FeeMaster.remarks
                ).filter(
                    FeeMaster.regd_no == student.regd_no,
                    db.func.lower(FeeMaster.fee_type).like(f'%{normalized_type}%')
                ).all()
                
                # Process each fee entry
                for fee_entry in fee_entries:
                    # Get standardized fee type label
                    std_fee_type = get_standardized_fee_type_label(fee_entry.fee_type)
                    
                    # Skip if we've already processed this standardized fee type for this student
                    if std_fee_type in processed_std_fee_types:
                        continue
                    
                    # Mark as processed
                    processed_std_fee_types.add(std_fee_type)
                    
                    # Get paid amount for this fee type
                    payment = db.session.query(
                        db.func.sum(Payment.amount_paid).label('total_paid')
                    ).filter(
                        db.func.lower(Payment.fee_type).like(f'%{normalized_type}%')
                    ).first()
                    
                    # Set default paid amount to 0 if no payment exists
                    paid_amount = payment.total_paid if payment and payment.total_paid else 0
                    
                    # Convert to float to ensure proper math operations
                    try:
                        fee_amount = float(fee_entry.amount)
                        paid_amount = float(paid_amount)
                    except (ValueError, TypeError):
                        app.logger.warning(f"Invalid amount value for student {student.regd_no}, fee type {fee_entry.fee_type}")
                        fee_amount = 0
                        paid_amount = 0
                    
                    # Determine payment status
                    is_not_paid = paid_amount == 0
                    is_partially_paid = 0 < paid_amount < fee_amount
                    
                    # Check if this student matches our payment status filter
                    should_include = (
                        (payment_status == 'not_paid' and is_not_paid) or 
                        (payment_status == 'partially_paid' and is_partially_paid) or
                        (payment_status == 'all' and (is_not_paid or is_partially_paid))
                    )
                    
                    if should_include:
                        # Create a unique key using registration number and standardized fee type
                        key = f"{student.regd_no}_{std_fee_type}"
                        
                        # Create student record
                        student_record = {
                            'regd_no': student.regd_no,
                            'name': student.name,
                            'batch_year': student.batch_year,
                            'branch': student.branch,
                            'mobile': student.mobile,
                            'fee_type': std_fee_type,
                            'total_amount': fee_amount,
                            'paid_amount': paid_amount,
                            'remaining': fee_amount - paid_amount,
                            'payment_status': 'Partially Paid' if paid_amount > 0 else 'Not Paid',
                            'remarks': fee_entry.remarks or ''
                        }
                        unpaid_students_dict[key] = student_record
        
        # Convert dictionary to list
        unpaid_students_list = list(unpaid_students_dict.values())
        
        # Sort by batch_year, registration number, and fee type
        unpaid_students_list.sort(key=lambda x: (x['batch_year'], x['regd_no'], x['fee_type']))
        
        # Debug logging
        app.logger.info(f"Found {len(unpaid_students_list)} records with payment status: {payment_status}")
        
        # If no students found, show message (unless downloading)
        if not unpaid_students_list and not (download_excel or download_csv):
            flash("No students match the selected criteria.", "info")
        
        # Handle download requests
        if download_excel:
            return generate_excel_report(unpaid_students_list, batch_year, branch, payment_status)
        elif download_csv:
            return generate_csv_report(unpaid_students_list, batch_year, branch, payment_status)
        
        # Render template with all necessary data
        return render_template('unpaid_students.html', 
                            unpaid_students=unpaid_students_list,
                            batch_years=batch_years,
                            fee_types=fee_types,
                            branches=branches,
                            selected_batch_year=batch_year,
                            selected_fee_type=fee_type,
                            selected_branch=branch,
                            selected_payment_status=payment_status)
                            
    except Exception as e:
        app.logger.error(f"Error retrieving unpaid students: {str(e)}")
        import traceback
        traceback_str = traceback.format_exc()
        app.logger.error(traceback_str)
        flash(f"Error retrieving unpaid students: {str(e)}", "error")
        # Include debug info in the template for development
        return render_template('unpaid_students.html', 
                              error=str(e),
                              unpaid_students=[],
                              batch_years=[],
                              fee_types=[],
                              branches=[],
                              debug_info=traceback_str if app.config.get('DEBUG', False) else None)

def generate_csv_report(unpaid_students_list, batch_year, branch, payment_status):
    """Generate CSV report of unpaid students"""
    import csv
    from io import StringIO
    from datetime import datetime
    from flask import Response
    
    # Create a string buffer to write CSV data
    output = StringIO()
    writer = csv.writer(output)
    
    # Write header row
    writer.writerow(['Registration No', 'Name', 'Batch Year', 'Branch', 
                     'Mobile', 'Fee Type', 'Total Amount', 'Payment Status', 'Remarks'])
    
    # Write data rows
    for student in unpaid_students_list:
        writer.writerow([
            student['regd_no'],
            student['name'],
            student['batch_year'],
            student['branch'],
            student['mobile'],
            student['fee_type'],
            student['total_amount'],
            student['payment_status'],
            student['remarks']
        ])
    
    # Generate timestamp for filename
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # Create filter description for filename
    filter_desc = []
    if batch_year:
        filter_desc.append(f"Batch{batch_year}")
    if branch:
        filter_desc.append(f"{branch}")
    if payment_status != 'all':
        status_label = "NotPaid" if payment_status == 'not_paid' else "PartiallyPaid"
        filter_desc.append(status_label)
    
    filter_str = "_".join(filter_desc) if filter_desc else "All"
    
    # Create filename
    filename = f"Unpaid_Students_{filter_str}_{timestamp}.csv"
    
    # Create response with CSV data
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename={filename}"}
    )

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out', 'info')
    return redirect(url_for('admin_login'))

@app.route('/chart-debug')
def chart_debug():
    """Helper route to debug chart generation"""
    try:
        # Import matplotlib here to check if it's available
        import matplotlib
        
        charts = create_visualizations()
        return jsonify({
            "success": True,
            "available_charts": list(charts.keys()),
            "charts_data": {
                k: bool(v) for k, v in charts.items() if k != "error"
            },
            "matplotlib_backend": matplotlib.get_backend()
        })
    except Exception as e:
        import traceback
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        })

@app.route('/update_remarks', methods=['POST'])
@login_required
def update_remarks():
    """Update remarks for a student's fee entry"""
    if request.method != 'POST':
        return jsonify({'success': False, 'error': 'Invalid request method'})
    
    try:
        # Get JSON data
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'})
        
        reg_no = data.get('registration_number')
        fee_type = data.get('fee_type')
        remarks = data.get('remarks', '')
        
        if not reg_no:
            return jsonify({'success': False, 'error': 'No registration number provided'})
        
        # If fee_type is 'all', update remarks for all fee types for this student
        if fee_type == 'all':
            fee_entries = FeeMaster.query.filter_by(regd_no=reg_no).all()
        else:
            # If specific fee type, use the normalized fee type for matching
            normalized_fee_type = normalize_fee_type(fee_type)
            fee_entries = FeeMaster.query.filter(
                FeeMaster.regd_no == reg_no,
                db.func.lower(FeeMaster.fee_type).like(f'%{normalized_fee_type}%')
            ).all()
        
        if not fee_entries:
            return jsonify({'success': False, 'error': 'No matching fee entries found'})
        
        # Update remarks for all matching entries
        for entry in fee_entries:
            entry.remarks = remarks
        
        # Commit the changes
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error updating remarks: {str(e)}")
        import traceback
        app.logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)})

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    app.logger.error(f"500 error: {str(e)}")
    return render_template('500.html'), 500

@app.route('/api/students', methods=['GET'])
@login_required
def api_students():
    """API endpoint to search for students by registration number or name"""
    query = request.args.get('query', '')
    if not query or len(query) < 3:
        return jsonify([])
    
    try:
        # Search for students matching the query in either reg_no or name
        students = db.session.query(
            Student.regd_no,
            Student.name,
            Student.batch_year
        ).filter(
            db.or_(
                Student.regd_no.like(f'%{query}%'),
                Student.name.like(f'%{query}%')
            )
        ).limit(10).all()
        
        # Convert to list of dicts
        result = [
            {'regd_no': s.regd_no, 'name': s.name, 'batch_year': s.batch_year}
            for s in students
        ]
        return jsonify(result)
    except Exception as e:
        app.logger.error(f"Error searching for students: {str(e)}")
        return jsonify([])

@app.route('/api/student-fees', methods=['GET'])
@login_required
def api_student_fees():
    """API endpoint to get fee information for a specific student"""
    regd_no = request.args.get('regd_no', '')
    if not regd_no:
        return jsonify([])
    
    try:
        # Get all fee entries for this student
        fee_entries = db.session.query(
            FeeMaster.fee_type,
            FeeMaster.amount,
            FeeMaster.remarks
        ).filter(
            FeeMaster.regd_no == regd_no
        ).all()
        
        results = []
        
        # For each fee entry, get paid amount and calculate remaining
        for entry in fee_entries:
            fee_type = entry.fee_type
            normalized_type = normalize_fee_type(fee_type)
            display_name = get_standardized_fee_type_label(fee_type)
            
            # Get total paid amount for this fee type
            payment = db.session.query(
                db.func.sum(Payment.amount_paid).label('total_paid')
            ).filter(
                db.func.lower(Payment.fee_type).like(f'%{normalized_type}%')
            ).first()
            
            paid_amount = payment.total_paid if payment and payment.total_paid else 0
            
            results.append({
                'fee_type': fee_type,
                'fee_type_display': display_name,
                'amount': float(entry.amount),
                'paid_amount': float(paid_amount),
                'remarks': entry.remarks or ''
            })
        
        return jsonify(results)
    except Exception as e:
        app.logger.error(f"Error getting student fees: {str(e)}")
        return jsonify([])

@app.route('/debug-chart-data')
@login_required
def debug_chart_data():
    """Debug endpoint to show raw data behind charts"""
    try:
        # Query payments data similar to what's used in charts
        payments_query = db.session.query(
            Student.batch_year,
            Payment.fee_type,
            db.func.sum(Payment.amount_paid).label('total_paid')
        ).join(
            Student, 
            Payment.regd_no == Student.regd_no
        ).filter(
            Student.batch_year == '2023-2027',
            db.func.lower(Payment.fee_type).like('%crt%')
        ).group_by(
            Student.batch_year,
            Payment.fee_type
        ).all()
        
        # Format the results for display
        results = []
        for row in payments_query:
            std_fee_type = get_standardized_fee_type_label(row.fee_type)
            results.append({
                'batch_year': row.batch_year,
                'fee_type': row.fee_type,
                'standardized_fee_type': std_fee_type,
                'amount_paid': float(row.total_paid)
            })
        
        # Also include the individual payments that make up these totals
        detailed_payments = db.session.query(
            Student.batch_year,
            Student.regd_no,
            Student.name,
            Payment.fee_type,
            Payment.amount_paid,
            Payment.date
        ).join(
            Student,
            Payment.regd_no == Student.regd_no
        ).filter(
            Student.batch_year == '2023-2027',
            db.func.lower(Payment.fee_type).like('%crt%')
        ).order_by(
            Payment.date
        ).all()
        
        payment_details = [
            {
                'batch_year': p.batch_year,
                'regd_no': p.regd_no,
                'name': p.name,
                'fee_type': p.fee_type,
                'amount': float(p.amount_paid),
                'date': p.date.strftime('%Y-%m-%d') if p.date else "Unknown"
            }
            for p in detailed_payments
        ]
        
        return render_template('debug_chart.html', 
                              summary_data=results, 
                              payment_details=payment_details)
        
    except Exception as e:
        app.logger.error(f"Error in debug chart data: {str(e)}")
        import traceback
        app.logger.error(traceback.format_exc())
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()})

@app.route('/api/calculate-distribution', methods=['POST'])
@login_required
def calculate_distribution():
    """Calculate how a payment amount would be distributed"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        regd_no = data.get('regd_no')
        fee_types = data.get('fee_types', [])
        total_amount = float(data.get('amount', 0))
        
        if not regd_no or not fee_types:
            return jsonify({'error': 'Missing required parameters'}), 400
        
        # Get fee details for the selected fee types
        fee_details = []
        total_fee_amount = 0
        
        for fee_type in fee_types:
            normalized_type = normalize_fee_type(fee_type)
            
            # Get fee master entry
            fee_entry = db.session.query(
                FeeMaster.fee_type,
                FeeMaster.amount
            ).filter(
                FeeMaster.regd_no == regd_no,
                db.func.lower(FeeMaster.fee_type).like(f'%{normalized_type}%')
            ).first()
            
            if fee_entry:
                fee_amount = float(fee_entry.amount)
                total_fee_amount += fee_amount
                
                # Get existing payments
                payment = db.session.query(
                    db.func.sum(Payment.amount_paid).label('total_paid')
                ).filter(
                    Payment.regd_no == regd_no,
                    db.func.lower(Payment.fee_type).like(f'%{normalized_type}%')
                ).first()
                
                paid_amount = payment.total_paid if payment and payment.total_paid else 0
                
                fee_details.append({
                    'fee_type': fee_entry.fee_type,
                    'display_name': get_standardized_fee_type_label(fee_entry.fee_type),
                    'amount': fee_amount,
                    'paid': float(paid_amount),
                    'remaining': max(0, fee_amount - float(paid_amount))
                })
        
        # Calculate distribution based on original fee amounts, not remaining amounts
        distribution = []
        total_distributed = 0
        
        for i, fee in enumerate(fee_details):
            if total_fee_amount > 0:
                proportion = fee['amount'] / total_fee_amount
                distributed_amount = round(total_amount * proportion, 2)
            else:
                # Equal distribution as fallback
                distributed_amount = round(total_amount / len(fee_details), 2)
            
            # Last item gets the remainder to avoid rounding issues
            if i == len(fee_details) - 1:
                distributed_amount = round(total_amount - total_distributed, 2)
                
            total_distributed += distributed_amount
            
            distribution.append({
                'fee_type': fee['fee_type'],
                'display_name': fee['display_name'],
                'original_amount': fee['amount'],
                'paid_amount': fee['paid'],
                'remaining': fee['remaining'],
                'proportion': fee['amount'] / total_fee_amount if total_fee_amount > 0 else 0,
                'distribution': distributed_amount
            })
        
        return jsonify({
            'success': True,
            'total_amount': total_amount,
            'total_fee_amount': total_fee_amount,
            'distribution': distribution
        })
        
    except Exception as e:
        app.logger.error(f"Error calculating distribution: {str(e)}")
        import traceback
        app.logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500
    

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    # If already logged in, redirect to dashboard
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = 'remember' in request.form
        
        admin = Admin.query.filter_by(username=username).first()
        
        if admin and admin.check_password(password):
            # Valid admin login
            login_user(admin, remember=remember)
            admin.last_login = datetime.utcnow()
            db.session.commit()
            
            flash('Admin login successful', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid admin credentials', 'danger')
    
    return render_template('admin_login.html')  # Fix the template name - there was a typo with "admin_login=.html"'

@app.route('/api/registrations-by-batch', methods=['GET'])
@login_required
def get_registrations_by_batch():
    """API endpoint to get all registration numbers for a specific batch year"""
    try:
        batch_year = request.args.get('batch_year', '')
        if not batch_year:
            return jsonify([])
        
        # Query students by batch year
        students = db.session.query(
            Student.regd_no,
            Student.name
        ).filter(
            Student.batch_year == batch_year
        ).order_by(
            Student.name
        ).all()
        
        # Convert to list of dicts
        result = [
            {'regd_no': s.regd_no, 'name': s.name}
            for s in students
        ]
        
        return jsonify(result)
    except Exception as e:
        app.logger.error(f"Error fetching registration numbers: {str(e)}")
        return jsonify([])

@app.route('/delete_paid_students', methods=['GET', 'POST'])
@login_required
def delete_paid_students():
    if request.method == 'POST':
        try:
            # Get the batch_year filter if provided
            batch_year = request.form.get('batch_year', '')
            fee_type = request.form.get('fee_type', '')
            confirm = request.form.get('confirm', '') == 'yes'
            
            if not confirm:
                flash('Please confirm deletion by checking the confirmation box.', 'warning')
                return redirect(url_for('delete_paid_students'))
            
            # Step 1: Find all fee master entries for fully paid students
            fee_entries_query = FeeMaster.query
            
            # Apply batch year filter if provided
            if batch_year:
                fee_entries_query = fee_entries_query.join(
                    Student, 
                    FeeMaster.regd_no == Student.regd_no
                ).filter(Student.batch_year == batch_year)
            
            # Apply fee type filter if provided
            if fee_type:
                normalized_fee_type = normalize_fee_type(fee_type)
                fee_entries_query = fee_entries_query.filter(
                    db.func.lower(FeeMaster.fee_type).like(f'%{normalized_fee_type}%')
                )
            
            # Get all fee entries
            fee_entries = fee_entries_query.all()
            
            # Track which entries are fully paid
            fully_paid_entries = []
            
            for entry in fee_entries:
                fee_amount = float(entry.amount)
                
                # Get payment info for this entry
                payment = db.session.query(
                    db.func.sum(Payment.amount_paid).label('total_paid')
                ).filter(
                    Payment.regd_no == entry.regd_no,
                    db.func.lower(Payment.fee_type).like(f'%{normalize_fee_type(entry.fee_type)}%')
                ).first()
                
                # Calculate total paid amount
                paid_amount = float(payment.total_paid) if payment and payment.total_paid else 0
                
                # Check if fully paid (with small epsilon to handle floating point comparison)
                if paid_amount >= (fee_amount - 0.01):
                    fully_paid_entries.append(entry)
            
            if not fully_paid_entries:
                flash('No fully paid student records found matching the criteria.', 'info')
                return redirect(url_for('delete_paid_students'))
            
            # Count students to be deleted (unique registration numbers)
            unique_reg_nos = set(entry.regd_no for entry in fully_paid_entries)
            
            # Begin deletion process within a transaction
            deleted_entries = 0
            deleted_payments = 0
            try:
                # Step 2: Delete related payment records
                for entry in fully_paid_entries:
                    # Delete payment records for this student and fee type
                    payment_result = db.session.query(Payment).filter(
                        Payment.regd_no == entry.regd_no,
                        db.func.lower(Payment.fee_type).like(f'%{normalize_fee_type(entry.fee_type)}%')
                    ).delete(synchronize_session=False)
                    
                    deleted_payments += payment_result
                
                # Step 3: Delete fee master entries
                for entry in fully_paid_entries:
                    db.session.delete(entry)
                    deleted_entries += 1
                
                # Commit changes
                db.session.commit()
                
                # Success message with deletion counts
                flash(f'Successfully deleted {deleted_entries} fee records and {deleted_payments} payment records '
                      f'for {len(unique_reg_nos)} fully paid students.', 'success')
                
                return redirect(url_for('dashboard'))
                
            except Exception as e:
                db.session.rollback()
                app.logger.error(f"Error deleting paid student data: {str(e)}")
                flash(f'Error deleting records: {str(e)}', 'error')
        
        except Exception as e:
            app.logger.error(f"Error in delete_paid_students: {str(e)}")
            import traceback
            app.logger.error(traceback.format_exc())
            flash(f'An error occurred: {str(e)}', 'error')
    
    # Get batch years for the filter dropdown
    batch_years = [year[0] for year in db.session.query(Student.batch_year).distinct().order_by(Student.batch_year).all()]
    fee_types = ['CRT', 'Phase 2', 'Phase 3']
    
    return render_template('delete_paid_students.html', batch_years=batch_years, fee_types=fee_types)


if __name__ == '__main__':
    app.run(debug=True)