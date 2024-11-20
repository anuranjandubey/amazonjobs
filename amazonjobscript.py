import urllib3
import json
from datetime import datetime, timedelta
from urllib.parse import urlencode, quote_plus
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import os
from pymongo import MongoClient, ASCENDING
from pymongo.server_api import ServerApi
import sys
import ssl
import certifi

class AmazonJobsTracker:
    def __init__(self):
        # MongoDB Atlas connection
        try:
            mongodb_uri = os.environ.get('MONGODB_URI', '')
            if not mongodb_uri:
                raise ValueError("MongoDB URI not found in environment variables")
            
            print("Connecting to MongoDB Atlas...")
            # Modified connection parameters
            self.client = MongoClient(
                mongodb_uri,
                server_api=ServerApi('1'),
                ssl=True,
                ssl_cert_reqs=ssl.CERT_REQUIRED,
                ssl_ca_certs=certifi.where(),
                connect_timeout=30000,
                socketTimeoutMS=None,
                connectTimeoutMS=30000,
                retryWrites=True,
                w="majority"
            )
            
            # Test the connection
            self.client.admin.command('ping')
            print("Successfully connected to MongoDB Atlas!")
            
            self.db = self.client['amazon_jobs']
            self.seen_jobs_collection = self.db['seen_jobs']
            self.initialize_ttl_index()
            
        except Exception as e:
            print(f"Error connecting to MongoDB: {str(e)}")
            print(f"MongoDB URI being used (without password): {self.mask_connection_string(mongodb_uri)}")
            raise
        
        # Email configuration
        self.smtp_server = 'smtp.gmail.com'
        self.smtp_port = 587
        self.email_address = os.environ['EMAIL_ADDRESS']
        self.email_password = os.environ['EMAIL_PASSWORD']
        self.cc_email = os.environ['CC_EMAIL']
        self.bcc_recipients = os.environ['BCC_RECIPIENTS'].split(',')

    @staticmethod
    def mask_connection_string(uri):
        """Mask sensitive information in connection string for logging"""
        if not uri:
            return "No URI provided"
        try:
            # Replace password with ***
            parts = uri.split('@')
            if len(parts) > 1:
                credentials = parts[0].split(':')
                if len(credentials) > 2:
                    masked_uri = f"{credentials[0]}:***@{parts[1]}"
                    return masked_uri
            return uri.replace(uri.split(':')[2].split('@')[0], '***')
        except:
            return "Error masking URI"
        
    def initialize_ttl_index(self):
        """Initialize TTL index to automatically remove old job entries after 30 days"""
        try:
            # List all indexes
            existing_indexes = list(self.seen_jobs_collection.list_indexes())
            ttl_index_exists = False
            
            # Check for existing TTL index
            for index in existing_indexes:
                if "created_at" in index["key"]:
                    ttl_index_exists = True
                    break
            
            if not ttl_index_exists:
                print("Creating TTL index for job tracking...")
                self.seen_jobs_collection.create_index(
                    [("created_at", ASCENDING)],
                    expireAfterSeconds=30 * 24 * 60 * 60,  # 30 days
                    background=True
                )
                print("TTL index created successfully!")
            else:
                print("TTL index already exists")
                
        except Exception as e:
            print(f"Error managing TTL index: {e}")
            print("Continuing without TTL index...")

    def is_recent_posting(self, posted_date_str, days=1):
        """Check if the job was posted within the last specified days"""
        try:
            posted_date = datetime.strptime(posted_date_str, "%B %d, %Y")
            current_date = datetime.now()
            difference = current_date - posted_date
            return difference.days <= days
        except Exception as e:
            print(f"Error parsing date {posted_date_str}: {e}")
            return False

    def generate_html_content(self, new_jobs):
        """Generate HTML email content for new jobs"""
        html = """
        <html>
        <head>
            <style>
                body { font-family: Arial, sans-serif; }
                .job { margin: 20px 0; padding: 15px; border: 1px solid #ddd; border-radius: 5px; }
                .job-title { color: #232F3E; font-size: 18px; font-weight: bold; }
                .job-details { margin: 10px 0; }
                .apply-button {
                    background-color: #FF9900;
                    color: white;
                    padding: 10px 20px;
                    text-decoration: none;
                    border-radius: 3px;
                    display: inline-block;
                }
            </style>
        </head>
        <body>
            <h2>🚀 New Amazon Entry-Level Software Jobs 🚀</h2>
            <p>Oyeee Jobs Agaiiy oyeeeee!!! Apply karo benchoooo!!!</p>
        """
        
        for job in new_jobs:
            html += f"""
            <div class="job">
                <div class="job-title">{job['title']}</div>
                <div class="job-details">
                    <p><strong>📍 Location:</strong> {job['location']}</p>
                    <p><strong>📅 Posted Date:</strong> {job['posted_date']}</p>
                    <p><strong>📊 Level:</strong> {job.get('level', 'N/A')}</p>
                    <p><strong>📝 Basic Qualifications:</strong> {job.get('basic_qualifications', 'N/A')}</p>
                </div>
                <a href="https://www.amazon.jobs/en/jobs/{job['id_icims']}" class="apply-button">Apply Now 🔥</a>
            </div>
            """
        
        html += """
        </body>
        </html>
        """
        return html

    def send_email(self, new_jobs):
        """Send email with new job listings"""
        if not new_jobs:
            print("No new jobs to send email about.")
            return
        
        msg = MIMEMultipart('alternative')
        msg['From'] = self.email_address
        msg['To'] = self.email_address
        msg['Cc'] = self.cc_email
        msg['Subject'] = f"Oye {len(new_jobs)} New Amazon Jobs Agaiiy oyeeeee!!!"
        
        html_content = self.generate_html_content(new_jobs)
        msg.attach(MIMEText(html_content, 'html'))
        
        try:
            # Save jobs data to CSV
            csv_content = "Title,Location,Posted Date,Level,Job ID,Apply Link\n"
            for job in new_jobs:
                csv_content += f"\"{job['title']}\",\"{job['location']}\",\"{job['posted_date']}\",\"{job.get('level', 'N/A')}\",\"{job['id_icims']}\",\"https://www.amazon.jobs/en/jobs/{job['id_icims']}\"\n"
            
            csv_filename = "amazon_new_jobs.csv"
            with open(csv_filename, 'w') as f:
                f.write(csv_content)
            
            # Attach CSV file
            with open(csv_filename, 'rb') as attachment:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(attachment.read())
                encoders.encode_base64(part)
                part.add_header(
                    'Content-Disposition',
                    f'attachment; filename={csv_filename}',
                )
                msg.attach(part)
            
            # Connect to the server and send email
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.email_address, self.email_password)
            
            all_recipients = [self.cc_email] + self.bcc_recipients
            server.sendmail(self.email_address, all_recipients, msg.as_string())
            server.quit()
            
            print(f"Email sent successfully with {len(new_jobs)} new jobs!")
            print(f"Recipients: CC -> {self.cc_email}, BCC -> {', '.join(self.bcc_recipients)}")
            
            os.remove(csv_filename)
            
        except Exception as e:
            print(f"Error sending email: {e}")

    def check_new_jobs(self):
        """Check for new jobs and send email if found"""
        searches = [
            "software dev engineer",
            "software developer 2025",
            "system dev engineer",
            "entry level software 2025",
            "system development engineer",
            "graduate software engineer 2025",
            "university graduate software 2025",
            "sde 2025"
        ]
        
        new_jobs = []
        http = urllib3.PoolManager()
        
        for search_term in searches:
            print(f"\nSearching for: {search_term}")
            
            params = {
                "normalized_country_code[]": "USA",
                "offset": 0,
                "result_limit": 20,
                "sort": "recent",
                "country": "USA",
                "base_query": search_term,
                "category[]": ["software-development"],
                "experience[]": ["entry-level"],
                "level[]": ["entry-level"],
                "posted_within[]": ["1d"],
            }
            
            try:
                query_string = urlencode(params, doseq=True)
                url = f"https://www.amazon.jobs/en/search.json?{query_string}"
                
                response = http.request('GET', url, headers={
                    "Accept": "application/json",
                    "Accept-Language": "en-US,en;q=0.5",
                })
                
                if response.status == 200:
                    data = json.loads(response.data.decode('utf-8'))
                    jobs = data.get("jobs", [])
                    
                    for job in jobs:
                        job_id = job['id_icims']
                        if (not self.is_job_seen(job_id) and 
                            self.is_recent_posting(job['posted_date'])):
                            new_jobs.append(job)
                            self.mark_job_seen(job_id)
                
            except Exception as e:
                print(f"Error with search term '{search_term}': {e}")
                print(f"Full error details: {str(e)}")
        
        # Remove duplicates while preserving order
        unique_new_jobs = []
        seen = set()
        for job in new_jobs:
            if job['id_icims'] not in seen:
                seen.add(job['id_icims'])
                unique_new_jobs.append(job)
        
        if unique_new_jobs:
            self.send_email(unique_new_jobs)
            print(f"\nFound {len(unique_new_jobs)} new jobs!")
        else:
            print("\nNo new jobs found.")

    def test_mongodb_connection():
        """Test MongoDB connection separately"""
        try:
            print("Testing MongoDB connection...")
            mongodb_uri = os.environ.get('MONGODB_URI', '')
            if not mongodb_uri:
                raise ValueError("MongoDB URI not found in environment variables")
            
            print(f"MongoDB URI format (masked): {AmazonJobsTracker.mask_connection_string(mongodb_uri)}")
            
            client = MongoClient(
                mongodb_uri,
                server_api=ServerApi('1'),
                ssl=True,
                ssl_cert_reqs=ssl.CERT_REQUIRED,
                ssl_ca_certs=certifi.where(),
                connect_timeout=30000,
                socketTimeoutMS=None,
                connectTimeoutMS=30000,
                retryWrites=True,
                w="majority"
            )
            
            # Test connection
            client.admin.command('ping')
            print("MongoDB connection test successful!")
            client.close()
            return True
        except Exception as e:
            print(f"MongoDB connection test failed: {str(e)}")
            return False

def main():
    tracker = None
    try:
        # First test MongoDB connection
        if not test_mongodb_connection():
            print("MongoDB connection test failed. Exiting...")
            sys.exit(1)
            
        print("\nStarting Amazon Jobs Tracker...")
        tracker = AmazonJobsTracker()
        tracker.check_new_jobs()
        print("\nJob check completed successfully!")
        
    except Exception as e:
        print(f"Error in main execution: {str(e)}")
        raise
    finally:
        # Close MongoDB connection
        if tracker and hasattr(tracker, 'client'):
            try:
                tracker.client.close()
                print("MongoDB connection closed.")
            except Exception as e:
                print(f"Error closing MongoDB connection: {e}")

if __name__ == "__main__":
    main()
