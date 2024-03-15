from flask import Flask, request, render_template, jsonify
from english_words import get_english_words_set
from random import choice, randint, sample, seed, randrange
from datetime import datetime
from io import BytesIO
from time import process_time
import uuid
import sys
import shutil
import subprocess
import json 
import zipfile
import os
import importlib.util
import random
from urllib.parse import quote_plus
from pymongo import MongoClient
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Retrieve variables
username = os.getenv('DB_USERNAME')
password = os.getenv('DB_PASSWORD')
host = os.getenv('DB_HOST')
port = os.getenv('DB_PORT')
db_name = os.getenv('DB_NAME')

# Encode username and password
encoded_username = quote_plus(username)
encoded_password = quote_plus(password)

# Construct MongoDB URI
uri = f"mongodb+srv://{encoded_username}:{encoded_password}@{host}/{db_name}?retryWrites=true&w=majority"

# Connecting to the MongoDB server
client = MongoClient(uri)
db = client[db_name]

app = Flask(__name__)

# Define the base directory where you want the files/directories to be created
BASE_DIR = os.path.expanduser('~/l-store-grader')
SUBMISSIONS_DIR = os.path.join(BASE_DIR, "submissions")

# Ensure the submissions directory exists
os.makedirs(SUBMISSIONS_DIR, exist_ok=True)

def generate_unique_name():
    words_list = list(get_english_words_set(['web2'], lower=True, alpha=True))

    filtered_words_list = [word for word in words_list if len(word) <= 5]

    chosen_words = random.sample(filtered_words_list, 3)
    
    chosen_words = [word.capitalize() for word in chosen_words]

    name = " ".join(chosen_words)
    
    return name
    
def milestone_tests(extract_path, lstore_path, milestone_name):
    milestones_collection = db['milestones']
    milestone_script = milestones_collection.find_one({"milestone": milestone_name})
    if milestone_script:
        script_code = milestone_script['code']

        # Create tester.py script
        tester_script_path = os.path.join(extract_path, 'tester.py')
        with open(tester_script_path, 'w') as tester_script:
            tester_script.write(script_code)
            
        # Set up environment variable to include the 'lstore' directory in PYTHONPATH
        env = os.environ.copy()
        env['PYTHONPATH'] = lstore_path + os.pathsep + env.get('PYTHONPATH', '')

        # Note the addition of cwd=extract_path to set the working directory
        result = subprocess.run(
            ["python", tester_script_path],
            capture_output=True,
            text=True,
            env=env,
            cwd=extract_path  # Set the current working directory to the unique folder
        )

        # Process the output
        if result.returncode == 0:
            output = json.loads(result.stdout)
        
        return output["results"], output["tests"], output["count"], output["total"]

@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        file = request.files['file']
        milestone = request.form.get('milestone')
        extended = request.form.get('extended')
        modification = ""
        submissions_collection = db['lstore']
        if file and file.filename.endswith('.zip'):
            # Change the current working directory to BASE_DIR
            os.chdir(SUBMISSIONS_DIR)
            # Generate a unique directory name
            unique_id = uuid.uuid4().hex
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            unique_folder_name = f"lstore_modules_{timestamp}_{unique_id}"
            extract_path = os.path.join(SUBMISSIONS_DIR, unique_folder_name)
            
            # Create the unique folder and the 'lstore' subfolder
            lstore_path = os.path.join(extract_path, 'lstore')
            if not os.path.exists(lstore_path):
                os.makedirs(lstore_path)

            # Proceed with file extraction
            with zipfile.ZipFile(BytesIO(file.read())) as zip_file:
                # Extract each file from the zip
                for member in zip_file.infolist():
                    # Build the path for extraction
                    filename = os.path.basename(member.filename)  # Ignore directory structure
                    if not filename:  # Skip directories
                        continue
                    
                    # Build the full path for the file to be extracted to
                    extraction_path = os.path.join(lstore_path, filename)
                    
                    # Extract the file
                    source = zip_file.open(member)
                    target = open(extraction_path, "wb")
                    with source, target:
                        shutil.copyfileobj(source, target)

            unique_file = generate_unique_name()
            if extended:
                modification = "_extended"
                if milestone == "milestone1":
                    results, m_tests, m_count, total = milestone_tests(extract_path, lstore_path, "milestone1_extended")
                elif milestone == "milestone2":
                    results, m_tests, m_count, total = milestone_tests(extract_path, lstore_path, "milestone2_extended")
            else:
                if milestone == "milestone1":
                    results, m_tests, m_count, total = milestone_tests(extract_path, lstore_path, "milestone1")
                elif milestone == "milestone2":
                    results, m_tests, m_count, total = milestone_tests(extract_path, lstore_path, "milestone2")
                elif milestone == "milestone3":
                    results, m_tests, m_count, total = milestone_tests(extract_path, lstore_path, "milestone3")

            milestone += modification
            submission_results = {
                'name': unique_file,
                'results': results,
                'tests': m_tests,
                'count': m_count,
                'total': total,
                'milestone_name': milestone,
                'timestamp': datetime.now()
            }

            # Store in MongoDB
            submission_id = submissions_collection.insert_one(submission_results).inserted_id

            return jsonify({
                'success': True,
                'name': unique_file,
                'results': results,  # Dictionary containing performance times
                'tests': m_tests,  # Dictionary of test names and outcomes
                'count': m_count,
                'total': total
            })
        
        else:
            return render_template('dashboard.html')

    return render_template('dashboard.html')

@app.route('/leaderboard', methods=['GET'])
def leaderboard():
    return render_template('results.html')

@app.route('/results/<milestone_name>', methods=['GET'])
def show_results(milestone_name):
    submissions_collection = db['lstore']
    # Use aggregation to calculate total time and sort submissions
    aggregation_pipeline = [
        # Filter by milestone_name
        {'$match': {'milestone_name': milestone_name}},
        # Add a field that calculates the total time and round it
        {'$addFields': {
            'total_time': {
                '$round': [{
                    '$add': [
                        '$results.insert_time',
                        '$results.select_time',
                        '$results.update_time',
                        '$results.delete_time',
                        '$results.agg_time'
                    ]
                }, 3]  # Round to 3 decimal places
            }
        }},
        # Ensure total_time is not 0
        {'$match': {'total_time': {'$gt': 0}}},
        # Sort by count in descending order, then by total_time in ascending order
        {'$sort': {'count': -1, 'total_time': 1}},
        # Limit to top 5 submissions
        {'$limit': 5},
        # Project to format the data as needed
        {'$project': {
            '_id': 0,  # Exclude the MongoDB _id field
            'name': 1,
            'count': 1,
            'total': 1,
            'total_time': 1
        }}
    ]
    top_submissions = submissions_collection.aggregate(aggregation_pipeline)
    
    submissions = list(top_submissions)
    # Return the data as JSON
    return jsonify(submissions)

if __name__ == "__main__":
    app.run(port="5400", debug=True)