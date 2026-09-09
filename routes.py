import sqlite3
from flask import Flask, render_template_string, request, redirect, url_for, flash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'test_key'
DB_NAME = 'todo.db'

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

@app.route('/')
def index():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT id, text FROM todos ORDER BY id DESC')
    todos = cursor.fetchall()
    conn.close()
    return render_template_string('''
<!DOCTYPE html>
<html>
<body>
    <h1>Todos</h1>
    <form action="/add" method="POST">
        <input type="text" name="task" required>
        <button type="submit">Add</button>
    </form>
    {% for error in get_flashed_messages() %}
        <p style="color:red">{{ error }}</p>
    {% endfor %}
    <ul>
        {% for todo in todos %}
        <li>
            {{ todo[1] }}
            <form action="/delete/{{ todo[0] }}" method="POST" style="display:inline">
                <button type="submit">Delete</button>
            </form>
        </li>
        {% endfor %}
    </ul>
</body>
</html>
''', todos=todos), 200

@app.route('/add', methods=['POST'])
def add_todo():
    task_text = request.form.get('task', '')
    if not task_text or not task_text.strip():
        flash('Task text cannot be empty.')
        return redirect(url_for('index'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO todos (text) VALUES (?)', (task_text.strip(),))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))
