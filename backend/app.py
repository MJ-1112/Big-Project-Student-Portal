import os
import re
import fitz
import pandas as pd
from flask import Flask, request, send_from_directory, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
import firebase_admin
from firebase_admin import credentials, firestore

# Setup
app = Flask(__name__)
CORS(app)
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Firebase Init
try:
    firebase_admin.get_app()
except ValueError:
    cred = credentials.Certificate('serviceAccountKey.json')
    firebase_admin.initialize_app(cred)

db = firestore.client()

@app.route('/extract-attendance', methods=['POST'])
def extract_attendance():
    if 'pdf' not in request.files:
        return jsonify({"error": "No PDF uploaded"}), 400

    file = request.files['pdf']
    filename = secure_filename(file.filename)
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    try:
        doc = fitz.open(filepath)
        data, subject_set = [], set()

        student_pattern = re.compile(
            r"(?P<SNo>\d+)\s+(?P<RegNo>RA\d+)\s+(?P<Name>[A-Z \n]+?)21MAB101T\(B\)", re.DOTALL)
        subject_attendance_pattern = re.compile(
            r"(21[A-Z]{3}\d{3}[TJ]?\([A-Z]\)?|21[A-Z]{3}\d{3}[TJ]?)\s+(\d{2,3}\.\d{2})"
        )

        for page in doc:
            text = page.get_text()
            matches = list(student_pattern.finditer(text))
            for i, match in enumerate(matches):
                block = text[match.end(): matches[i + 1].start() if i + 1 < len(matches) else len(text)]
                subjects = subject_attendance_pattern.findall(block)
                reg_no = match.group("RegNo")
                name = re.sub(r'\s+', ' ', match.group("Name")).strip()
                row = {
                    "S.No": int(match.group("SNo")),
                    "Reg No": reg_no,
                    "Name": name
                }
                for code, percent in subjects:
                    row[code] = float(percent)
                    subject_set.add(code)
                data.append(row)

        df = pd.DataFrame(data)
        for subject in subject_set:
            if subject not in df.columns:
                df[subject] = None
        df = df[["S.No", "Reg No", "Name"] + sorted(subject_set)]
        output_csv = os.path.join(UPLOAD_FOLDER, "student_attendance.csv")
        df.to_csv(output_csv, index=False)

        # Store in Firestore
        batch = db.batch()
        for _, row in df.iterrows():
            ref = db.collection('student_attendance').document(row['Reg No'])
            doc_data = {
                "SNo": row["S.No"],
                "Name": row["Name"],
                "RegNo": row["Reg No"],
                "last_updated": firestore.SERVER_TIMESTAMP
            }
            for subject in subject_set:
                doc_data[subject] = row[subject] if pd.notna(row[subject]) else None
            batch.set(ref, doc_data, merge=True)
        batch.commit()

        return jsonify({
            "message": "Upload + extraction complete.",
            "csv_filename": "student_attendance.csv",
            "download_url": f"/download/student_attendance.csv"
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
