from quart import Quart, request, jsonify, render_template, session, redirect, url_for
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
import os
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import pytz

app = Quart(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "your_secret_key")  # Make sure to set this in production

# MongoDB connection
client = AsyncIOMotorClient(os.environ.get("MONGODB_URL"))
db = client.floating_notes
notes_collection = db[os.environ.get('NOTES_COLLECTION_NAME', 'notes')]
users_collection = db['users']

@app.route('/')
async def index():
    if 'username' not in session:
        return redirect(url_for('login'))
    return await render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
async def login():
    if request.method == 'POST':
        data = await request.form
        username = data.get('username')
        password = data.get('password')
        user = await users_collection.find_one({'username': username})
        if user and check_password_hash(user['password'], password):
            session['username'] = username
            return redirect(url_for('index'))
        return 'Invalid username or password', 401
    return await render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
async def register():
    if request.method == 'POST':
        data = await request.form
        username = data.get('username')
        password = data.get('password')
        existing_user = await users_collection.find_one({'username': username})
        if existing_user:
            return 'Username already exists', 400
        hashed_password = generate_password_hash(password)
        await users_collection.insert_one({'username': username, 'password': hashed_password})
        return redirect(url_for('login'))
    return await render_template('register.html')

@app.route('/logout')
async def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route("/status")
async def status_handler():
    return jsonify({"status": "ok"})

@app.route('/api/notes', methods=['POST'])
async def add_note():
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = await request.json
    content = data['content']
    
    # Check word limit (approximately 400 characters)
    if len(content) > 400:
        return jsonify({'error': 'Note exceeds 400 character limit'}), 400
    
    kolkata_tz = pytz.timezone('Asia/Kolkata')
    current_time = datetime.now(kolkata_tz)
    note = {
        'content': content,
        'likes': 0,
        'dislikes': 0,
        'username': session['username'],
        'timestamp': current_time.strftime('%Y-%m-%d %H:%M:%S %Z'),
        'pinned': False
    }
    result = await notes_collection.insert_one(note)
    note['_id'] = str(result.inserted_id)
    return jsonify(note), 201

@app.route('/api/notes', methods=['GET'])
async def get_notes():
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    search_query = request.args.get('search', '')
    query = {
        "username": session['username'],
        "content": {"$regex": search_query, "$options": "i"}
    } if search_query else {"username": session['username']}
    notes = await notes_collection.find(query).sort([("pinned", -1), ("timestamp", -1)]).to_list(length=None)
    return jsonify([{**note, '_id': str(note['_id'])} for note in notes])

@app.route('/api/notes/<note_id>/pin', methods=['POST'])
async def pin_note(note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Unpin all notes
    await notes_collection.update_many(
        {'username': session['username']},
        {'$set': {'pinned': False}}
    )
    
    # Pin the selected note
    await notes_collection.update_one(
        {'_id': ObjectId(note_id), 'username': session['username']},
        {'$set': {'pinned': True}}
    )
    
    return '', 204

@app.route('/api/notes/<note_id>', methods=['PUT'])
async def update_note(note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = await request.json
    await notes_collection.update_one({'_id': ObjectId(note_id), 'username': session['username']}, {'$set': {'content': data['content']}})
    return '', 204

@app.route('/api/notes/<note_id>', methods=['DELETE'])
async def delete_note(note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    await notes_collection.delete_one({'_id': ObjectId(note_id), 'username': session['username']})
    return '', 204

@app.route('/api/notes/<note_id>/like', methods=['POST'])
async def like_note(note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    await notes_collection.update_one({'_id': ObjectId(note_id), 'username': session['username']}, {'$inc': {'likes': 1}})
    return '', 204

@app.route('/api/notes/<note_id>/dislike', methods=['POST'])
async def dislike_note(note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    await notes_collection.update_one({'_id': ObjectId(note_id), 'username': session['username']}, {'$inc': {'dislikes': 1}})
    return '', 204

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)), debug=True)