from flask import Flask, render_template, request, redirect, url_for, session, flash, render_template_string
import mysql.connector
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'your_secret_key'

UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(os.path.join('static', 'defaults'), exist_ok=True)  # ensure fallback dir exists


def get_db():
    return mysql.connector.connect(
        host='localhost',
        user='root',
        password='',
        database='bad_db',
        autocommit=False
    )


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def humanize_timedelta(dt):
    now = datetime.utcnow()
    if not isinstance(dt, datetime):
        return "some time"
    diff = now - dt
    seconds = diff.total_seconds()
    if seconds < 60:
        return f"{int(seconds)} seconds"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)} minutes"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)} hours"
    days = hours / 24
    if days < 7:
        return f"{int(days)} days"
    weeks = days / 7
    if weeks < 4:
        return f"{int(weeks)} weeks"
    months = days / 30
    if months < 12:
        return f"{int(months)} months"
    years = days / 365
    return f"{int(years)} years"


@app.context_processor
def inject_user_context():
    context = {}
    if session.get('username'):
        db = get_db()
        cursor = db.cursor(dictionary=True)
        try:
            username = session.get('username')

            # Prefer an in-session image (set immediately after upload) so the UI shows
            # the new avatar on redirect without a timing issue. We'll still query the DB
            # for other counts, and fall back to the DB profile_image only if the
            # session override is missing.
            user_image = session.get('user_image')

            # Fetch profile image from DB only if we don't already have one in session
            cursor.execute("SELECT profile_image FROM users WHERE username = %s", (username,))
            user_row = cursor.fetchone()
            db_image = user_row.get('profile_image') if user_row else None

            if not user_image:
                user_image = db_image

            # Fallback if missing
            if not user_image:
                user_image = 'defaults/avatar.png'

            # Normalize path separators to forward slashes so url_for('static', filename=...) works
            if isinstance(user_image, str):
                user_image = user_image.replace('\\', '/')

            # Debug logging (optional)
            app.logger.debug(f"user_image for {username}: {user_image}")
            full_path = os.path.join(app.static_folder, user_image)
            app.logger.debug(f"[DEBUG] exists on disk? {os.path.exists(full_path)} -> {full_path}")

            # Unread notifications
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM notifications WHERE username = %s AND (is_read = 0 OR is_read IS NULL)",
                (username,)
            )
            unread_row = cursor.fetchone()
            unread_notifications = unread_row['cnt'] if unread_row else 0

            # Pending applications
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM applications WHERE applicant_username = %s",
                (username,)
            )
            pending_row = cursor.fetchone()
            pending_applications = pending_row['cnt'] if pending_row else 0

            # Last applied
            cursor.execute("""
                SELECT j.title, j.posted_by, a.applied_at
                FROM applications a
                JOIN jobs j ON a.job_id = j.id
                WHERE a.applicant_username = %s
                ORDER BY a.applied_at DESC
                LIMIT 1
            """, (username,))
            last = cursor.fetchone()
            last_applied = None
            if last:
                last_applied = {
                    'position': last['title'],
                    'company': last['posted_by'],
                    'humanized_time': humanize_timedelta(last['applied_at'])
                }

            # New matches (jobs not applied to)
            cursor.execute("""
                SELECT COUNT(*) AS cnt
                FROM jobs j
                WHERE NOT EXISTS (
                  SELECT 1 FROM applications a
                  WHERE a.job_id = j.id AND a.applicant_username = %s
                )
            """, (username,))
            matches_row = cursor.fetchone()
            new_matches = matches_row['cnt'] if matches_row else 0

            context.update({
                'user_image': user_image,
                'new_matches': new_matches,
                'pending_applications': pending_applications,
                'unread_notifications': unread_notifications,
                'last_applied': last_applied,
                'username': username
            })
        finally:
            cursor.close()
            db.close()
    return context


@app.route('/')
def home():
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    msg = ''
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        db = get_db()
        cursor = db.cursor(dictionary=True)
        user = None
        try:
            cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
            user = cursor.fetchone()
        finally:
            cursor.close()
            db.close()

        if user and check_password_hash(user['password'], password):
            session['user_id'] = user.get('id') or user.get('user_id') or username
            session['username'] = user['username']
            return redirect(url_for('user_dashboard'))
        else:
            msg = "Incorrect username or password."

    return render_template('login.html', msg=msg)


@app.route('/register', methods=['GET', 'POST'])
def register():
    msg = ''
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
            if cursor.fetchone():
                msg = 'Username already exists.'
            else:
                hashed_password = generate_password_hash(password)
                cursor.execute("INSERT INTO users (username, password) VALUES (%s, %s)",
                               (username, hashed_password))
                db.commit()
                msg = 'Registration successful! You can now log in.'
        finally:
            cursor.close()
            db.close()
    return render_template('register.html', msg=msg)


@app.route('/profile')
def profile():
    if 'username' not in session:
        return redirect(url_for('login'))

    db = get_db()
    cursor = db.cursor(dictionary=True)
    user = None
    try:
        cursor.execute("SELECT * FROM users WHERE username = %s", (session['username'],))
        user = cursor.fetchone()
        # normalize stored path separators if present so templates build correct static URLs
        if user and user.get('profile_image'):
            user['profile_image'] = user['profile_image'].replace('\\', '/')
    finally:
        cursor.close()
        db.close()

    return render_template('profile.html', user=user)


@app.route('/edit-profile', methods=['GET', 'POST'])
def edit_profile():
    if 'username' not in session:
        return redirect(url_for('login'))

    db = get_db()
    cursor = db.cursor(dictionary=True)
    user = None
    try:
        if request.method == 'POST':
            update_fields = []
            update_values = []

            email = request.form.get('email')
            phone = request.form.get('phone')
            password = request.form.get('password')
            file = request.files.get('profile_image')

            if email:
                update_fields.append("email = %s")
                update_values.append(email)
            if phone:
                update_fields.append("phone = %s")
                update_values.append(phone)
            if password:
                hashed_password = generate_password_hash(password)
                update_fields.append("password = %s")
                update_values.append(hashed_password)
            if file and allowed_file(file.filename):
                original = secure_filename(file.filename)
                name, ext = os.path.splitext(original)
                timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
                filename = f"{name}_{timestamp}{ext}"
                relative_path = f"uploads/{filename}"
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(file_path)
                # store the relative path in the session as well so templates and debug tools
                # can immediately pick up the new avatar without requiring a new request that
                # triggers the context processor DB lookup.
                session['user_image'] = relative_path
                app.logger.debug(f"Saved profile image to {file_path}, storing path {relative_path}")
                update_fields.append("profile_image = %s")
                update_values.append(relative_path)

            if update_fields:
                update_values.append(session['username'])
                query = f"UPDATE users SET {', '.join(update_fields)} WHERE username = %s"
                cursor.execute(query, tuple(update_values))
                db.commit()
                flash("Profile updated successfully.")
                return redirect(url_for('profile'))

        cursor.execute("SELECT * FROM users WHERE username = %s", (session['username'],))
        user = cursor.fetchone()
        # normalize path separators if the DB contains Windows backslashes
        if user and user.get('profile_image'):
            user['profile_image'] = user['profile_image'].replace('\\', '/')
    finally:
        cursor.close()
        db.close()

    return render_template('edit_profile.html', user=user)


@app.route('/apply-jobs', methods=['GET'])
def apply_jobs():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    jobs = []
    try:
        cursor.execute("SELECT id, title, description, posted_by FROM jobs")
        jobs = cursor.fetchall()
    finally:
        cursor.close()
        db.close()
    return render_template('apply_jobs.html', jobs=jobs)


@app.route('/post-job', methods=['GET', 'POST'])
def post_job():
    if 'username' not in session:
        return redirect(url_for('login'))

    db = get_db()
    cursor = db.cursor()
    try:
        if request.method == 'POST':
            title = request.form['title']
            description = request.form['description']
            posted_by = session['username']

            cursor.execute("INSERT INTO jobs (title, description, posted_by) VALUES (%s, %s, %s)",
                           (title, description, posted_by))
            db.commit()

            cursor.execute("SELECT username FROM users WHERE username != %s", (posted_by,))
            users = cursor.fetchall()
            for user in users:
                uname = user[0] if isinstance(user, tuple) else user.get('username')
                cursor.execute("INSERT INTO notifications (username, message) VALUES (%s, %s)",
                               (uname, f"New job posted: {title}"))
            db.commit()

            flash("Job posted and users notified!")
            return redirect(url_for('user_dashboard'))
    finally:
        cursor.close()
        db.close()
    return render_template('post_job.html')


@app.route('/notifications')
def notifications():
    if 'username' not in session:
        return redirect(url_for('login'))

    db = get_db()
    cursor = db.cursor(dictionary=True)
    notices = []
    try:
        cursor.execute("SELECT * FROM notifications WHERE username = %s ORDER BY created_at DESC", (session['username'],))
        notices = cursor.fetchall()
        cursor.execute("UPDATE notifications SET is_read = 1 WHERE username = %s", (session['username'],))
        db.commit()
    finally:
        cursor.close()
        db.close()
    return render_template('notifications.html', notices=notices)


@app.route('/mark-all-read', methods=['POST'])
def mark_all_read():
    if 'username' not in session:
        return redirect(url_for('login'))

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("DELETE FROM notifications WHERE username = %s", (session['username'],))
        db.commit()
        flash('All notifications marked as read.')
    finally:
        cursor.close()
        db.close()
    return redirect(url_for('notifications'))


@app.route('/clear-notifications', methods=['POST'])
def clear_notifications():
    return mark_all_read()


@app.route('/apply-job/<int:job_id>', methods=['POST'])
def apply_job(job_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    username = session['username']
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT posted_by, title FROM jobs WHERE id = %s", (job_id,))
        job = cursor.fetchone()
        if not job:
            flash("Job not found.")
            return redirect(url_for('apply_jobs'))

        posted_by = job['posted_by']
        job_title = job['title']

        cursor.execute(
            "SELECT * FROM applications WHERE job_id = %s AND applicant_username = %s",
            (job_id, username)
        )
        if cursor.fetchone():
            flash("You have already applied for this job.")
        else:
            cursor.execute(
                "INSERT INTO applications (job_id, applicant_username) VALUES (%s, %s)",
                (job_id, username)
            )
            message = f"{username} has applied for your job: {job_title}"
            cursor.execute("INSERT INTO notifications (username, message) VALUES (%s, %s)", (posted_by, message))
            db.commit()
            flash("Applied successfully and the job poster has been notified!")
    finally:
        cursor.close()
        db.close()
    return redirect(url_for('apply_jobs'))


@app.route('/applied-jobs')
def applied_jobs():
    if 'username' not in session:
        return redirect(url_for('login'))

    db = get_db()
    cursor = db.cursor(dictionary=True)
    jobs = []
    try:
        cursor.execute("""
            SELECT a.id AS application_id, j.title, j.description, j.posted_by, a.applied_at
            FROM applications a
            JOIN jobs j ON a.job_id = j.id
            WHERE a.applicant_username = %s
            ORDER BY a.applied_at DESC
        """, (session['username'],))
        rows = cursor.fetchall()
        # convert DB rows to predictable dicts for templates
        jobs = []
        for r in rows:
            jobs.append({
                'application_id': r.get('application_id') if isinstance(r, dict) else r[0],
                'title': r.get('title') if isinstance(r, dict) else r[1],
                'description': r.get('description') if isinstance(r, dict) else r[2],
                'posted_by': r.get('posted_by') if isinstance(r, dict) else r[3],
                'applied_at': r.get('applied_at') if isinstance(r, dict) else r[4],
            })
    finally:
        cursor.close()
        db.close()
    # render template expecting `applications` variable
    return render_template('applied_jobs.html', applications=jobs)


@app.route('/cancel-application/<int:application_id>', methods=['POST'])
def cancel_application(application_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("DELETE FROM applications WHERE id = %s AND applicant_username = %s",
                       (application_id, session['username']))
        db.commit()
        flash("Application canceled.")
    finally:
        cursor.close()
        db.close()
    return redirect(url_for('applied_jobs'))


@app.route('/view-applicants')
def view_applicants():
    if 'username' not in session:
        return redirect(url_for('login'))

    db = get_db()
    cursor = db.cursor(dictionary=True)
    applicants = []
    try:
        cursor.execute("""
            SELECT a.applicant_username, j.title, a.applied_at
            FROM applications a
            JOIN jobs j ON a.job_id = j.id
            WHERE j.posted_by = %s
            ORDER BY a.applied_at DESC
        """, (session['username'],))
        applicants = cursor.fetchall()
    finally:
        cursor.close()
        db.close()
    return render_template('view_applicants.html', applicants=applicants)


@app.route('/admin/applications')
def admin_applications():
    if 'username' not in session:
        return redirect(url_for('login'))

    username = session['username']
    db = get_db()
    cursor = db.cursor(dictionary=True)
    applications = []
    try:
        cursor.execute("""
            SELECT a.id, a.job_id, j.title AS job_title, a.applicant_username, a.applied_at
            FROM applications a
            JOIN jobs j ON a.job_id = j.id
            WHERE j.posted_by = %s
            ORDER BY a.applied_at DESC
        """, (username,))
        applications = cursor.fetchall()
    finally:
        cursor.close()
        db.close()
    return render_template('admin_applications.html', applications=applications)


@app.route('/user')
def user_dashboard():
    if 'username' in session:
        return render_template('user_dashboard.html')
    else:
        return redirect(url_for('login'))

@app.route('/debug-avatar')
def debug_avatar():
    if 'username' not in session:
        return "No user logged in"
    user_image = inject_user_context().get('user_image')
    return render_template_string(
        "<p>Username: {{ session.username }}</p>"
        "<p>User image (from context): {{ user_image }}</p>"
        "<img src='{{ url_for(\"static\", filename=user_image) }}' style='max-width:200px;' "
        "onerror='this.onerror=null;this.src=\"{{ url_for(\"static\", filename=\"defaults/avatar.png\") }}\";'>",
        user_image=user_image
    )



@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


if __name__ == '__main__':
    app.run(debug=True)
