from quart import Quart, request, jsonify, render_template, session, redirect, url_for
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
import os
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import pytz
import random
import string

app = Quart(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "your_secret_key")  # Make sure to set this in production

# MongoDB connection
client = AsyncIOMotorClient(os.environ.get("MONGODB_URL"))
db = client.floating_notes
notes_collection = db[os.environ.get('NOTES_COLLECTION_NAME', 'notes')]
users_collection = db['users']
public_links_collection = db['public_links']


def generate_short_code(length=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

@app.route('/api/notes/<note_id>/create_public_link', methods=['POST'])
async def create_public_link(note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    note = await notes_collection.find_one({'_id': ObjectId(note_id), 'username': session['username']})
    if not note:
        return jsonify({'error': 'Note not found'}), 404
    
    short_code = generate_short_code()
    while await public_links_collection.find_one({'short_code': short_code}):
        short_code = generate_short_code()
    
    public_note = {
        'short_code': short_code,
        'content': note['content'],
        'username': session['username'],
        'timestamp': note['timestamp'],
        'original_note_id': str(note['_id'])  # Store the original note ID
    }
    
    result = await public_links_collection.insert_one(public_note)
    
    return jsonify({'public_link': f'/pub/{short_code}', 'public_note_id': str(result.inserted_id)}), 201

@app.route('/api/notes/<note_id>/delete_public_link', methods=['POST'])
async def delete_public_link(note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    note = await notes_collection.find_one({'_id': ObjectId(note_id), 'username': session['username']})
    if not note:
        return jsonify({'error': 'Note not found'}), 404
    
    result = await public_links_collection.delete_many({'username': session['username'], 'original_note_id': str(note['_id'])})
    
    if result.deleted_count == 0:
        return jsonify({'error': 'Public link not found'}), 404
    
    return jsonify({'message': f'Deleted {result.deleted_count} public link(s)'}), 200

@app.route('/api/public_notes/<public_note_id>', methods=['DELETE'])
async def delete_specific_public_note(public_note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    result = await public_links_collection.delete_one({
        '_id': ObjectId(public_note_id),
        'username': session['username']
    })
    
    if result.deleted_count == 0:
        return jsonify({'error': 'Public note not found or you do not have permission to delete it'}), 404
    
    return jsonify({'message': 'Public note deleted successfully'}), 200

@app.route('/api/public_notes', methods=['GET'])
async def get_public_notes():
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    public_notes = await public_links_collection.find({'username': session['username']}).to_list(length=None)
    return jsonify([{
        'id': str(note['_id']),
        'short_code': note['short_code'],
        'content': note['content'],
        'timestamp': note['timestamp'],
        'public_link': f'/pub/{note["short_code"]}'
    } for note in public_notes])

@app.route('/api/public_notes', methods=['POST'])
async def add_public_note():
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = await request.json
    content = data.get('content')
    
    if not content:
        return jsonify({'error': 'Content is required'}), 400
    
    # Check word limit (approximately 400 characters)
    if len(content) > 400:
        return jsonify({'error': 'Note exceeds 400 character limit'}), 400
    
    kolkata_tz = pytz.timezone('Asia/Kolkata')
    current_time = datetime.now(kolkata_tz)
    
    short_code = generate_short_code()
    while await public_links_collection.find_one({'short_code': short_code}):
        short_code = generate_short_code()
    
    public_note = {
        'short_code': short_code,
        'content': content,
        'username': session['username'],
        'timestamp': current_time.strftime('%Y-%m-%d %H:%M:%S %Z'),
        'likes': 0,
        'dislikes': 0
    }
    
    result = await public_links_collection.insert_one(public_note)
    public_note['_id'] = str(result.inserted_id)
    public_note['public_link'] = f'/pub/{short_code}'
    
    return jsonify(public_note), 201

@app.route('/pub/<short_code>')
async def public_note(short_code):
    public_note = await public_links_collection.find_one({'short_code': short_code})
    if not public_note:
        return 'Note not found', 404
    return await render_template('shareable.html', note=public_note)

@app.route('/pub')
async def pub_redirect():
    short_code = request.args.get('short_code')
    if not short_code:
        return 'Invalid link', 400
    return redirect(url_for('public_note', short_code=short_code))


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