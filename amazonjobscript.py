import urllib3
import json
from datetime import datetime, timedelta
from urllib.parse import urlencode
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import os
from pymongo.mongo_client import MongoClient
import sys
import certifi

class AmazonJobsTracker:
    def __init__(self):
        try:
            base_uri = os.environ.get('MONGODB_URI', '')
            if not base_uri:
                raise ValueError("MongoDB URI not found in environment variables")
            
            print("Connecting to MongoDB Atlas...")
            self.client = MongoClient(
                base_uri,
                tls=True,
                tlsCAFile=certifi.where()
            )
            
            self.client.admin.command('ping')
            print("Successfully connected to MongoDB!")
            
            self.db = self.client['amazon_jobs']
            self.seen_jobs_collection = self.db['seen_jobs']
            self.initialize_ttl_index()
            
            # Print current collection stats
            count = self.seen_jobs_collection.count_documents({})
            print(f"Currently tracking {count} jobs in database")
            
        except Exception as e:
            print(f"Error connecting to MongoDB: {str(e)}")
            raise

        self.smtp_server = 'smtp.gmail.com'
        self.smtp_port = 587
        self.email_address = os.environ['EMAIL_ADDRESS']
        self.email_password = os.environ['EMAIL_PASSWORD']
        self.cc_email = os.environ['CC_EMAIL']
        self.bcc_recipients = os.environ['BCC_RECIPIENTS'].split(',')

    def initialize_ttl_index(self):
        try:
            # Create TTL index on created_at field
            self.seen_jobs_collection.create_index(
                "created_at", 
                expireAfterSeconds=30 * 24 * 60 * 60  # 30 days
            )
            
            # Create regular index on job_id for faster lookups
            self.seen_jobs_collection.create_index("job_id", unique=True)
            print("Indexes created/verified successfully")
        except Exception as e:
            print(f"Error managing indexes: {e}")

    def is_recent_posting(self, posted_date_str, days=1):
        try:
            posted_date = datetime.strptime(posted_date_str, "%B %d, %Y")
            current_date = datetime.now()
            difference = current_date - posted_date
            is_recent = difference.days <= days
            print(f"Job posted on {posted_date}, {difference.days} days old, is_recent: {is_recent}")
            return is_recent
        except Exception as e:
            print(f"Error parsing date {posted_date_str}: {e}")
            return False

    def is_job_seen(self, job_id):
        try:
            result = self.seen_jobs_collection.find_one({"job_id": job_id})
            is_seen = result is not None
            print(f"Checking job {job_id}: {'previously seen' if is_seen else 'new job'}")
            return is_seen
        except Exception as e:
            print(f"Error checking job status: {e}")
            return False

    def mark_job_seen(self, job_id, job_data):
        try:
            # Create a more comprehensive job record
            job_record = {
                "job_id": job_id,
                "created_at": datetime.utcnow(),
                "last_seen": datetime.utcnow(),
                "title": job_data.get('title', ''),
                "location": job_data.get('location', ''),
                "posted_date": job_data.get('posted_date', ''),
                "level": job_data.get('level', ''),
                "url": f"https://www.amazon.jobs/en/jobs/{job_id}",
                "basic_qualifications": job_data.get('basic_qualifications', ''),
                "first_seen_date": datetime.utcnow()
            }
            
            # Use upsert with job_id as the unique identifier
            result = self.seen_jobs_collection.update_one(
                {"job_id": job_id},
                {"$set": job_record},
                upsert=True
            )
            
            if result.upserted_id:
                print(f"New job {job_id} recorded in database")
            else:
                print(f"Job {job_id} information updated")
                
        except Exception as e:
            print(f"Error marking job as seen: {e}")

    def check_new_jobs(self):
        """Check for new jobs and send email if found"""
        searches = [
            "software dev engineer",
            "software developer 2025",
            "software development engineer 2025",
            "new grad software 2025",
            "entry level software 2025",
            "software development engineer",
            "graduate software engineer 2025",
            "university graduate software 2025",
            "sde 2025"
        ]
        
        new_jobs = []
        http = urllib3.PoolManager()
        
        print("\nStarting job search...")
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
                    print(f"Found {len(jobs)} total jobs for search term: {search_term}")
                    
                    for job in jobs:
                        job_id = job['id_icims']
                        if self.is_recent_posting(job['posted_date']):
                            if not self.is_job_seen(job_id):
                                print(f"New job found: {job['title']} ({job_id})")
                                new_jobs.append(job)
                                self.mark_job_seen(job_id, job)
                            else:
                                print(f"Skipping previously seen job: {job['title']} ({job_id})")
                        else:
                            print(f"Skipping old job: {job['title']} ({job_id})")
                else:
                    print(f"Error: Received status code {response.status}")
                
            except Exception as e:
                print(f"Error with search term '{search_term}': {e}")
        
        # Remove duplicates while preserving order
        unique_new_jobs = []
        seen = set()
        for job in new_jobs:
            if job['id_icims'] not in seen:
                seen.add(job['id_icims'])
                unique_new_jobs.append(job)
        
        print(f"\nFound {len(unique_new_jobs)} unique new jobs")
        
        if unique_new_jobs:
            self.send_email(unique_new_jobs)
        else:
            print("No new jobs to send")

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
                    print(f"Found {len(jobs)} jobs for search term: {search_term}")
                    
                    for job in jobs:
                        job_id = job['id_icims']
                        if (not self.is_job_seen(job_id) and 
                            self.is_recent_posting(job['posted_date'])):
                            new_jobs.append(job)
                            self.mark_job_seen(job_id, job)
                            print(f"New job found: {job['title']} ({job_id})")
                else:
                    print(f"Error: Received status code {response.status}")
                
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
            print(f"\nSending email for {len(unique_new_jobs)} new jobs...")
            self.send_email(unique_new_jobs)
            print(f"Email sent successfully!")
        else:
            print("\nNo new jobs found.")

def test_mongodb_connection():
    """Test MongoDB connection separately"""
    try:
        print("Testing MongoDB connection...")
        uri = os.environ.get('MONGODB_URI', '')
        if not uri:
            raise ValueError("MongoDB URI not found in environment variables")

        print(f"Using certifi CA file from: {certifi.where()}")
        client = MongoClient(
            uri,
            tls=True,
            tlsCAFile=certifi.where()
        )
        
        client.admin.command('ping')
        print("Pinged your deployment. You successfully connected to MongoDB!")
        client.close()
        return True
    except Exception as e:
        print(f"MongoDB connection test failed: {str(e)}")
        return False

def main():
    tracker = None
    try:
        print("\nStarting Amazon Jobs Tracker...")
        print("Current time:", datetime.now())
        
        tracker = AmazonJobsTracker()
        
        # Print current database status
        count = tracker.seen_jobs_collection.count_documents({})
        recent_jobs = tracker.seen_jobs_collection.find().sort("created_at", -1).limit(5)
        
        print(f"\nCurrent database status:")
        print(f"Total tracked jobs: {count}")
        print("\nMost recent jobs in database:")
        for job in recent_jobs:
            print(f"- {job.get('title', 'No title')} ({job.get('job_id', 'No ID')}) - First seen: {job.get('first_seen_date')}")
        
        tracker.check_new_jobs()
        print("\nJob check completed successfully!")
        
    except Exception as e:
        print(f"Error in main execution: {str(e)}")
        raise
    finally:
        if tracker and hasattr(tracker, 'client'):
            tracker.client.close()
            print("MongoDB connection closed.")

if __name__ == "__main__":
    main()
