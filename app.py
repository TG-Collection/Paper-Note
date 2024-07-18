from quart import Quart, request, jsonify, render_template, session, redirect, url_for, abort
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
import os
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import pytz
import random
import string
from bson.errors import InvalidId

app = Quart(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "your_secret_key")  # Make sure to set this in production

# MongoDB connection
client = AsyncIOMotorClient(os.environ.get("MONGODB_URL"))
db = client.floating_notes
notes_collection = db[os.environ.get('NOTES_COLLECTION_NAME', 'notes')]
users_collection = db['users']
public_spaces_collection = db['public_spaces']

def generate_short_code(length=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

@app.route('/api/create_public_space', methods=['POST'])
async def create_public_space():
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = await request.json
    topic_name = data.get('topic_name', 'No Name')
    
    short_code = generate_short_code()  
    while await public_spaces_collection.find_one({'short_code': short_code}):
        short_code = generate_short_code()
    
    new_space = {
        'short_code': short_code,
        'creator': session['username'],
        'created_at': datetime.now(pytz.timezone('Asia/Kolkata')),
        'last_updated': datetime.now(pytz.timezone('Asia/Kolkata')),
        'topic_name': topic_name,
        'notes': []
    }
    
    result = await public_spaces_collection.insert_one(new_space)
    
    return jsonify({'public_link': f'/pub/{short_code}', 'space_id': str(result.inserted_id)}), 201

@app.route('/api/public_spaces', methods=['GET'])
async def list_public_spaces():
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    username = session['username']
    
    cursor = public_spaces_collection.find({'creator': username})
    
    public_spaces = []
    async for space in cursor:
        space['_id'] = str(space['_id'])
        public_spaces.append({
            'id': space['_id'],
            'short_code': space['short_code'],
            'created_at': space['created_at'].isoformat(),
            'creator': space['creator'],
            'topic_name': space.get('topic_name', 'No Name'),
            'note_count': len(space['notes'])
        })
    
    return jsonify(public_spaces), 200

@app.route('/api/public_spaces/<short_code>/edit_topic', methods=['PUT'])
async def edit_topic_name(short_code):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = await request.json
    new_topic_name = data.get('topic_name')
    
    if not new_topic_name:
        return jsonify({'error': 'Topic name is required'}), 400
    
    result = await public_spaces_collection.update_one(
        {'short_code': short_code, 'creator': session['username']},
        {'$set': {'topic_name': new_topic_name}}
    )
    
    if result.modified_count == 0:
        return jsonify({'error': 'Space not found or you do not have permission to edit it'}), 404
    
    return jsonify({'success': True, 'new_topic_name': new_topic_name}), 200

@app.route('/spaces')
async def spaces():
    if 'username' not in session:
        return redirect(url_for('login'))
    return await render_template('space.html')

async def get_participants(short_code):
    space = await public_spaces_collection.find_one({'short_code': short_code})
    if not space:
        return []
    # Initialize an empty list to hold usernames
    usernames = []
    # Check if 'notes' key exists and is a list
    if 'notes' in space and isinstance(space['notes'], list):
        # Iterate over each note in the 'notes' list
        for note in space['notes']:
            # Check if 'username' key exists in the note
            if 'username' in note:
                # Add the username to the usernames list
                usernames.append(note['username'])
    return usernames


@app.route('/api/public_spaces/<short_code>/notes', methods=['POST'])
async def add_public_note(short_code):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    space = await public_spaces_collection.find_one({'short_code': short_code})
    if not space:
        return jsonify({'error': 'Public space not found'}), 404
    
    data = await request.json
    content = data.get('content')
    
    if not content:
        return jsonify({'error': 'Content is required'}), 400
    
    if len(content) > 400:
        return jsonify({'error': 'Note exceeds 400 character limit'}), 400
    
    kolkata_tz = pytz.timezone('Asia/Kolkata')
    current_time = datetime.now(kolkata_tz)
    
    new_note = {
        '_id': ObjectId(),
        'content': content,
        'username': session['username'],
        'timestamp': current_time.strftime('%Y-%m-%d %H:%M:%S %Z'),
        'likes': 0,
        'dislikes': 0
    }
    result = await public_spaces_collection.update_one(
        {'short_code': short_code},
        {'$push': {'notes': new_note}}
    )  
    new_note['_id'] = str(new_note['_id'])
    return jsonify(new_note), 201

@app.route('/api/public_spaces/<short_code>/notes', methods=['GET'])
async def get_public_notes(short_code):
    space = await public_spaces_collection.find_one({'short_code': short_code})
    if not space:
        return jsonify({'error': 'Public space not found'}), 404
    serialized_notes = [{**note, '_id': str(note['_id'])} for note in space['notes']]
    return jsonify({
        'notes': serialized_notes,
        'topic': space.get('topic_name', 'Untitled Topic')
    })

@app.route('/api/public_spaces/<short_code>/notes/<note_id>/like', methods=['POST'])
async def like_public_note(short_code, note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        object_id = ObjectId(note_id)
    except InvalidId:
        return jsonify({'error': 'Invalid note ID'}), 400
    
    result = await public_spaces_collection.update_one(
        {'short_code': short_code, 'notes._id': object_id},
        {'$inc': {'notes.$.likes': 1}}
    )
    
    if result.modified_count == 0:
        return jsonify({'error': 'Note not found'}), 404
    
    return '', 204

@app.route('/api/public_spaces/<short_code>/notes/<note_id>/dislike', methods=['POST'])
async def dislike_public_note(short_code, note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        object_id = ObjectId(note_id)
    except InvalidId:
        return jsonify({'error': 'Invalid note ID'}), 400
    
    result = await public_spaces_collection.update_one(
        {'short_code': short_code, 'notes._id': object_id},
        {'$inc': {'notes.$.dislikes': 1}}
    )
    
    if result.modified_count == 0:
        return jsonify({'error': 'Note not found'}), 404
    
    return '', 204

@app.route('/api/public_spaces/<short_code>/notes/<note_id>/pin', methods=['POST'])
async def pin_public_note(short_code, note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        object_id = ObjectId(note_id)
    except InvalidId:
        return jsonify({'error': 'Invalid note ID'}), 400
    
    space = await public_spaces_collection.find_one({'short_code': short_code})
    if not space:
        return jsonify({'error': 'Public space not found'}), 404

    note_to_pin = next((note for note in space['notes'] if note['_id'] == object_id), None)
    if not note_to_pin or note_to_pin['username'] != session['username']:
        return jsonify({'error': 'Note not found or you do not have permission to pin it'}), 404

    new_pinned_status = not note_to_pin.get('pinned', False)

    if new_pinned_status:
        await public_spaces_collection.update_one(
            {'short_code': short_code},
            {'$set': {'notes.$[].pinned': False}}
        )

    result = await public_spaces_collection.update_one(
        {'short_code': short_code, 'notes._id': object_id},
        {'$set': {'notes.$.pinned': new_pinned_status}}
    )
    
    if result.modified_count == 0:
        return jsonify({'error': 'Failed to update note'}), 500
    
    return jsonify({'pinned': new_pinned_status}), 200

@app.route('/api/public_spaces/<short_code>/notes/<note_id>', methods=['DELETE'])
async def delete_public_note(short_code, note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        object_id = ObjectId(note_id)
    except InvalidId:
        return jsonify({'error': 'Invalid note ID'}), 400
    
    result = await public_spaces_collection.update_one(
        {'short_code': short_code},
        {'$pull': {'notes': {'_id': object_id, 'username': session['username']}}}
    )
    
    if result.modified_count == 0:
        return jsonify({'error': 'Note not found or you do not have permission to delete it'}), 404
    
    return '', 204

@app.route('/api/public_spaces/<short_code>', methods=['DELETE'])
async def delete_public_space(short_code):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    result = await public_spaces_collection.delete_one({'short_code': short_code, 'creator': session['username']})
    
    if result.deleted_count == 0:
        return jsonify({'error': 'Public space not found or you do not have permission to delete it'}), 404
    
    return '', 204

@app.route('/api/public_login', methods=['POST'])
async def public_login():
    try:
        data = await request.json
        username = data.get('username')
        password = data.get('password')
        
        if not username or not password:
            return jsonify({'error': 'Username and password are required'}), 400
        
        user = await users_collection.find_one({'username': username})
        if user and check_password_hash(user['password'], password):
            session['username'] = username
            return jsonify({'success': True, 'username': username}), 200
        return jsonify({'error': 'Invalid username or password'}), 401
    except Exception as e:
        print(f"Login error: {str(e)}")  # Log the error
        return jsonify({'error': 'An unexpected error occurred'}), 500

@app.route('/api/public_logout', methods=['POST'])
async def public_logout():
    session.pop('username', None)
    return jsonify({'success': True}), 200

@app.route('/api/public_register', methods=['POST'])
async def api_register():
    data = await request.json
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'error': 'Username and password are required'}), 400
    
    existing_user = await users_collection.find_one({'username': username})
    if existing_user:
        return jsonify({'error': 'Username already exists'}), 400
    
    hashed_password = generate_password_hash(password)
    await users_collection.insert_one({'username': username, 'password': hashed_password})
    
    session['username'] = username
    return jsonify({'success': True, 'username': username}), 201

@app.route('/pub/<short_code>')
async def public_space(short_code):
    space = await public_spaces_collection.find_one({'short_code': short_code})
    if not space:
        return await render_template('error.html', error_code=404, error_message="Invalid URL", error_description="Public space not found"), 404
    
    # Check if user is logged in
    if 'username' not in session:
        # If not logged in, redirect to the auth page with the current URL as the 'next' parameter
        return redirect(url_for('auth', next=f'/pub/{short_code}'))
    
    return await render_template('share.html', space=space)

@app.route('/auth')
async def auth():
    next_url = request.args.get('next', '/')
    return await render_template('auth.html', next_url=next_url)

@app.route('/')
async def index():
    if 'username' not in session:
        return redirect(url_for('login'))
    return await render_template('index.html')


@app.route('/about')
async def about():
    return await render_template('about.html')

@app.route('/login', methods=['GET', 'POST'])
async def login():
    if request.method == 'POST':
        data = await request.form
        username = data.get('username')
        password = data.get('password')
        user = await users_collection.find_one({'username': username})
        if user and check_password_hash(user['password'], password):
            session['username'] = username
            next_url = request.args.get('next', url_for('index'))
            return redirect(next_url)
        return 'Invalid username or password', 401
    return await render_template('login.html')

@app.route('/api/check_login')
async def check_login():
    if 'username' in session:
        return jsonify({'logged_in': True, 'username': session['username']})
    return jsonify({'logged_in': False}), 401

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

@app.errorhandler(404)
async def not_found_error(error):
    return await render_template('error.html', error_code=404, error_message="Page Not Found", error_description="Sorry, the page you are looking for does not exist. It might have been moved or deleted."), 404

@app.errorhandler(500)
async def internal_error(error):
    return await render_template('error.html', error_code=500, error_message="Internal Server Error", error_description="The server encountered an internal error and was unable to complete your request."), 500

@app.errorhandler(Exception)
async def unhandled_exception(e):
    return await render_template('error.html', error_code=500, error_message="Unexpected Error", error_description=f"An unexpected error occurred: {str(e)}"), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)), debug=True)