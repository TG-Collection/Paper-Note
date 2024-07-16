from quart import Quart, request, jsonify, render_template
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
import os

app = Quart(__name__)

# MongoDB connection
client = AsyncIOMotorClient(os.environ.get("MONGODB_URL"))
db = client.floating_notes
notes_collection = db[os.environ.get('COLLECTION_NAME', 'notes')]

@app.route('/')
async def index():
    return await render_template('index.html')

@app.route("/status")
async def status_handler():
    return jsonify({"status": "ok"})

@app.route('/api/notes', methods=['GET'])
async def get_notes():
    search_query = request.args.get('search', '')
    query = {"content": {"$regex": search_query, "$options": "i"}} if search_query else {}
    notes = await notes_collection.find(query).to_list(length=None)
    return jsonify([{**note, '_id': str(note['_id'])} for note in notes])

@app.route('/api/notes', methods=['POST'])
async def add_note():
    data = await request.json
    note = {
        'content': data['content'],
        'likes': 0,
        'dislikes': 0
    }
    result = await notes_collection.insert_one(note)
    note['_id'] = str(result.inserted_id)
    return jsonify(note), 201

@app.route('/api/notes/<note_id>', methods=['PUT'])
async def update_note(note_id):
    data = await request.json
    await notes_collection.update_one({'_id': ObjectId(note_id)}, {'$set': {'content': data['content']}})
    return '', 204

@app.route('/api/notes/<note_id>', methods=['DELETE'])
async def delete_note(note_id):
    await notes_collection.delete_one({'_id': ObjectId(note_id)})
    return '', 204

@app.route('/api/notes/<note_id>/like', methods=['POST'])
async def like_note(note_id):
    await notes_collection.update_one({'_id': ObjectId(note_id)}, {'$inc': {'likes': 1}})
    return '', 204

@app.route('/api/notes/<note_id>/dislike', methods=['POST'])
async def dislike_note(note_id):
    await notes_collection.update_one({'_id': ObjectId(note_id)}, {'$inc': {'dislikes': 1}})
    return '', 204

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)), debug=True)
