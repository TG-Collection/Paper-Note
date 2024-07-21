from quart import Quart, request, jsonify, render_template, session, redirect, url_for, abort
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey, Text, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.future import select
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import pytz
import random
import string
import os
import psutil
import time
import socket
import asyncio
from sqlalchemy.exc import IntegrityError
import time
from ratelimit import rate_limit

# Add this at the top of your file
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


start_time = time.time()

app = Quart(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "your_secret_key")  # Make sure to set this in production

# MySQL connection
DATABASE_URL = os.environ.get("DATABASE_URL")
engine = create_async_engine(DATABASE_URL, echo=True)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False)
    password = Column(String(255), nullable=False)

class PublicSpace(Base):
    __tablename__ = 'public_spaces'
    id = Column(Integer, primary_key=True)
    short_code = Column(String(6), unique=True, nullable=False)
    creator = Column(String(50), ForeignKey('users.username'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    topic_name = Column(String(255), nullable=False)
    locked = Column(Boolean, default=False)
    hidden = Column(Boolean, default=False)
    notes = relationship('Note', back_populates='space')

class Note(Base):
    __tablename__ = 'notes'
    id = Column(Integer, primary_key=True)
    content = Column(Text, nullable=False)
    username = Column(String(50), ForeignKey('users.username'), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    likes = Column(Integer, default=0)
    dislikes = Column(Integer, default=0)
    space_id = Column(Integer, ForeignKey('public_spaces.id'))
    space = relationship('PublicSpace', back_populates='notes')
    pinned = Column(Boolean, default=False)

class Status(Base):
    __tablename__ = 'status'
    id = Column(Integer, primary_key=True)
    ip = Column(String(15), nullable=False)
    last_seen = Column(DateTime, default=datetime.utcnow)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

def generate_short_code(length=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

@app.before_serving
async def startup():
    await init_db()

@app.after_serving
async def shutdown():
    await engine.dispose()

@app.route('/api/public_spaces/<short_code>/toggle_hide', methods=['POST'])
async def toggle_hide_space(short_code):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    async with async_session() as sess:
        space = await sess.execute(select(PublicSpace).filter_by(short_code=short_code, creator=session['username']))
        space = space.scalar_one_or_none()
        if not space:
            return jsonify({'error': 'Unauthorized'}), 401
        
        space.hidden = not space.hidden
        await sess.commit()
    
    return jsonify({'hidden': space.hidden}), 200


@app.route('/api/create_public_space', methods=['POST'])
@rate_limit(limit=1, per=60)  # Limit to 1 request per minute
async def create_public_space():
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = await request.json
    topic_name = data.get('topic_name', 'No Name')
    
    short_code = generate_short_code()
    async with async_session() as sess:
        async with sess.begin():
            try:
                while await sess.execute(select(PublicSpace).filter_by(short_code=short_code)):
                    short_code = generate_short_code()
                
                new_space = PublicSpace(
                    short_code=short_code,
                    creator=session['username'],
                    topic_name=topic_name
                )
                sess.add(new_space)
                await sess.flush()  # This will assign an ID to new_space if it's auto-incrementing
                
                logger.info(f"Created new public space: {new_space.id} with short code: {short_code}")
                
                return jsonify({'public_link': f'/go/{short_code}', 'space_id': new_space.id}), 201
            except IntegrityError:
                logger.error(f"Failed to create public space due to integrity error. Topic: {topic_name}")
                return jsonify({'error': 'Failed to create public space. Please try again.'}), 500
            

@app.route('/api/public_spaces', methods=['GET'])
async def list_public_spaces():
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    username = session['username']
    
    async with async_session() as sess:
        result = await sess.execute(select(PublicSpace).filter_by(creator=username))
        spaces = result.scalars().all()
    
    public_spaces = [{
        'id': space.id,
        'short_code': space.short_code,
        'created_at': space.created_at.isoformat(),
        'creator': space.creator,
        'topic_name': space.topic_name,
        'note_count': len(space.notes)
    } for space in spaces]
    
    return jsonify(public_spaces), 200

@app.route('/api/public_spaces/<short_code>/edit_topic', methods=['PUT'])
async def edit_topic_name(short_code):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = await request.json
    new_topic_name = data.get('topic_name')
    
    if not new_topic_name:
        return jsonify({'error': 'Topic name is required'}), 400
    
    async with async_session() as sess:
        result = await sess.execute(
            select(PublicSpace).filter_by(short_code=short_code, creator=session['username'])
        )
        space = result.scalar_one_or_none()
        if not space:
            return jsonify({'error': 'Space not found or you do not have permission to edit it'}), 404
        
        space.topic_name = new_topic_name
        await sess.commit()
    
    return jsonify({'success': True, 'new_topic_name': new_topic_name}), 200

@app.route('/api/public_spaces/<short_code>/notes', methods=['POST'])
async def add_public_note(short_code):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    async with async_session() as sess:
        space = await sess.execute(select(PublicSpace).filter_by(short_code=short_code))
        space = space.scalar_one_or_none()
        if not space:
            return jsonify({'error': 'Public space not found'}), 404
        
        if space.locked and space.creator != session['username']:
            return jsonify({'error': 'This space is locked'}), 403

        data = await request.json
        content = data.get('content')
        
        if not content:
            return jsonify({'error': 'Content is required'}), 400
        
        if len(content) > 1000:
            return jsonify({'error': 'Note exceeds 1000 character limit'}), 400
        
        kolkata_tz = pytz.timezone('Asia/Kolkata')
        current_time = datetime.now(kolkata_tz)
        
        new_note = Note(
            content=content,
            username=session['username'],
            timestamp=current_time,
            space=space
        )
        sess.add(new_note)
        await sess.commit()
        await sess.refresh(new_note)
    
    return jsonify({
        'id': new_note.id,
        'content': new_note.content,
        'username': new_note.username,
        'timestamp': new_note.timestamp.strftime('%Y-%m-%d %H:%M:%S %Z'),
        'likes': new_note.likes,
        'dislikes': new_note.dislikes
    }), 201

@app.route('/api/public_spaces/<short_code>/notes/<int:note_id>', methods=['PATCH'])
async def edit_public_note(short_code, note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    async with async_session() as sess:
        space = await sess.execute(select(PublicSpace).filter_by(short_code=short_code))
        space = space.scalar_one_or_none()
        if not space:
            return jsonify({'error': 'Public space not found'}), 404

        if space.locked and space.creator != session['username']:
            return jsonify({'error': 'This space is locked'}), 403

        data = await request.json
        new_content = data.get('content')
        if not new_content:
            return jsonify({'error': 'New content is required'}), 400

        if len(new_content) > 1000:
            return jsonify({'error': 'Note exceeds 1000 character limit'}), 400

        note = await sess.execute(select(Note).filter_by(id=note_id, space_id=space.id))
        note = note.scalar_one_or_none()
        if not note or note.username != session['username']:
            return jsonify({'error': 'Note not found or you do not have permission to edit it'}), 404

        note.content = new_content
        await sess.commit()

    return jsonify({'message': 'Note updated successfully'}), 200

@app.route('/api/public_spaces/<short_code>/toggle_lock', methods=['POST'])
async def toggle_lock(short_code):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    async with async_session() as sess:
        space = await sess.execute(select(PublicSpace).filter_by(short_code=short_code, creator=session['username']))
        space = space.scalar_one_or_none()
        if not space:
            return jsonify({'error': 'Unauthorized'}), 401
        
        space.locked = not space.locked
        await sess.commit()
    
    return jsonify({'locked': space.locked}), 200

@app.route('/api/public_spaces/<short_code>/notes')
async def get_public_space_notes(short_code):
    async with async_session() as sess:
        space = await sess.execute(select(PublicSpace).filter_by(short_code=short_code))
        space = space.scalar_one_or_none()
        if not space:
            return jsonify({'error': 'Space not found'}), 404
        
        if space.hidden and session.get('username') != space.creator:
            return jsonify({'error': 'Space is hidden'}), 403
        
        await sess.refresh(space, attribute_names=['notes'])
        
        notes = [{
            'id': note.id,
            'content': note.content,
            'username': note.username,
            'timestamp': note.timestamp.strftime('%Y-%m-%d %H:%M:%S %Z'),
            'likes': note.likes,
            'dislikes': note.dislikes
        } for note in space.notes]
        
        return jsonify({
            'topic': space.topic_name,
            'creator': space.creator,
            'locked': space.locked,
            'hidden': space.hidden,
            'notes': notes
        })

@app.route('/api/public_spaces/<short_code>/notes/<int:note_id>/like', methods=['POST'])
async def like_public_note(short_code, note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    async with async_session() as sess:
        note = await sess.execute(select(Note).join(PublicSpace).filter(PublicSpace.short_code == short_code, Note.id == note_id))
        note = note.scalar_one_or_none()
        if not note:
            return jsonify({'error': 'Note not found'}), 404
        
        note.likes += 1
        await sess.commit()
    
    return '', 204

@app.route('/api/public_spaces/<short_code>/notes/<int:note_id>/dislike', methods=['POST'])
async def dislike_public_note(short_code, note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    async with async_session() as sess:
        note = await sess.execute(select(Note).join(PublicSpace).filter(PublicSpace.short_code == short_code, Note.id == note_id))
        note = note.scalar_one_or_none()
        if not note:
            return jsonify({'error': 'Note not found'}), 404
        
        note.dislikes += 1
        await sess.commit()
    
    return '', 204

@app.route('/api/public_spaces/<short_code>/notes/<int:note_id>', methods=['DELETE'])
async def delete_public_note(short_code, note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    async with async_session() as sess:
        note = await sess.execute(select(Note).join(PublicSpace).filter(
            PublicSpace.short_code == short_code,
            Note.id == note_id,
            Note.username == session['username']
        ))
        note = note.scalar_one_or_none()
        if not note:
            return jsonify({'error': 'Note not found or you do not have permission to delete it'}), 404
        
        await sess.delete(note)
        await sess.commit()
    
    return '', 204

@app.route('/api/public_spaces/<short_code>', methods=['DELETE'])
async def delete_public_space(short_code):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    async with async_session() as sess:
        space = await sess.execute(select(PublicSpace).filter_by(short_code=short_code, creator=session['username']))
        space = space.scalar_one_or_none()
        if not space:
            return jsonify({'error': 'Public space not found or you do not have permission to delete it'}), 404
        
        await sess.delete(space)
        await sess.commit()
    
    return '', 204

@app.route('/go/<short_code>')
async def public_space(short_code):
    async with async_session() as sess:
        space = await sess.execute(select(PublicSpace).filter_by(short_code=short_code))
        space = space.scalar_one_or_none()
        if not space:
            abort(404)
        if space.hidden and session.get('username') != space.creator:
            return await render_template('hide.html')
    
    # Render the normal space view
    return await render_template('share.html', short_code=short_code)

@app.route('/api/notes', methods=['POST'])
async def add_note():
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = await request.json
    content = data['content']
    
    if len(content) > 1000:
        return jsonify({'error': 'Note exceeds 1000 character limit'}), 400
    
    kolkata_tz = pytz.timezone('Asia/Kolkata')
    current_time = datetime.now(kolkata_tz)
    
    new_note = Note(
        content=content,
        username=session['username'],
        timestamp=current_time
    )
    
    async with async_session() as sess:
        sess.add(new_note)
        await sess.commit()
        await sess.refresh(new_note)
    
    return jsonify({
        'id': new_note.id,
        'content': new_note.content,
        'username': new_note.username,
        'timestamp': new_note.timestamp.strftime('%Y-%m-%d %H:%M:%S %Z')
    }), 201

@app.route('/api/notes', methods=['GET'])
async def get_notes():
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    search_query = request.args.get('search', '')
    
    async with async_session() as sess:
        query = select(Note).filter(Note.username == session['username'])
        if search_query:
            query = query.filter(Note.content.ilike(f'%{search_query}%'))
        query = query.order_by(Note.pinned.desc(), Note.timestamp.desc())
        result = await sess.execute(query)
        notes = result.scalars().all()
    
    return jsonify([{
        'id': note.id,
        'content': note.content,
        'username': note.username,
        'timestamp': note.timestamp.strftime('%Y-%m-%d %H:%M:%S %Z'),
        'pinned': note.pinned
    } for note in notes])

@app.route('/api/notes/<int:note_id>', methods=['PUT'])
async def update_note(note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = await request.json
    
    async with async_session() as sess:
        note = await sess.execute(select(Note).filter_by(id=note_id, username=session['username']))
        note = note.scalar_one_or_none()
        if not note:
            return jsonify({'error': 'Note not found or you do not have permission to edit it'}), 404
        
        note.content = data['content']
        await sess.commit()
    
    return '', 204

@app.route('/api/notes/<int:note_id>', methods=['DELETE'])
async def delete_note(note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    async with async_session() as sess:
        note = await sess.execute(select(Note).filter_by(id=note_id, username=session['username']))
        note = note.scalar_one_or_none()
        if not note:
            return jsonify({'error': 'Note not found or you do not have permission to delete it'}), 404
        
        await sess.delete(note)
        await sess.commit()
    
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

@app.route('/login', methods=['GET', 'POST'])
async def login():
    if request.method == 'POST':
        form = await request.form
        username = form.get('username')
        password = form.get('password')
        
        async with async_session() as sess:
            result = await sess.execute(select(User).filter_by(username=username))
            user = result.scalar_one_or_none()
            if user and check_password_hash(user.password, password):
                session['username'] = username
                next_url = request.args.get('next', url_for('index'))
                return redirect(next_url)
        return 'Invalid username or password', 401
    return await render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
async def register():
    if request.method == 'POST':
        form = await request.form
        username = form.get('username')
        password = form.get('password')
        
        async with async_session() as sess:
            existing_user = await sess.execute(select(User).filter_by(username=username))
            if existing_user.scalar_one_or_none():
                return 'Username already exists', 400
            
            hashed_password = generate_password_hash(password)
            new_user = User(username=username, password=hashed_password)
            sess.add(new_user)
            await sess.commit()
        
        return redirect(url_for('login'))
    return await render_template('register.html')

@app.route('/api/check_login')
async def check_login():
    if 'username' in session:
        return jsonify({'logged_in': True, 'username': session['username']})
    return jsonify({'logged_in': False}), 401

@app.route('/logout')
async def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/')
async def index():
    if 'username' not in session:
        return redirect(url_for('login'))
    return await render_template('index.html')

@app.route('/about')
async def about():
    return await render_template('about.html')

@app.route('/hide')
async def hidden_space():
    return await render_template('hide.html')

@app.route('/spaces')
async def spaces():
    if 'username' not in session:
        return redirect(url_for('login'))
    return await render_template('space.html')

def get_service_running_time():
    running_time = time.time() - start_time
    return str(timedelta(seconds=int(running_time)))

@app.route('/status')
async def status():
    return await render_template('status.html')

@app.route('/api/status')
async def api_status():
    async with async_session() as sess:
        total_users = await sess.scalar(select(func.count()).select_from(User))
        total_spaces = await sess.scalar(select(func.count()).select_from(PublicSpace))
    
    cpu_load = psutil.cpu_percent()
    ram_usage = psutil.virtual_memory().percent
    disk_usage = psutil.disk_usage('/').percent
    
    hostname = socket.gethostname()
    current_ip = socket.gethostbyname(hostname)
    
    async with async_session() as sess:
        status = await sess.execute(select(Status).filter_by(ip=current_ip))
        status = status.scalar_one_or_none()
        if status:
            status.last_seen = datetime.now()
        else:
            status = Status(ip=current_ip)
            sess.add(status)
        await sess.commit()
    
    status_data = {
        'total_users': total_users,
        'current_ip': current_ip,
        'total_spaces': total_spaces,
        'service_running_time': get_service_running_time(),
        'cpu_load': f"{cpu_load:.1f}%",
        'ram_usage': f"{ram_usage:.1f}%",
        'disk_usage': f"{disk_usage:.1f}%"
    }
    return jsonify(status_data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))