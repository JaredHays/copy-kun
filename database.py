#!/usr/bin/env python3

import codecs
import configparser
import json
import logging
import os
import praw
import sys
import time
from datetime import datetime, timedelta, timezone
from peewee import *

# Set up logging 
logging.basicConfig(filename = os.path.join(sys.path[0], "copykun.log"), format = "%(asctime)s %(message)s")
logger = logging.getLogger("copykun")
logger.setLevel(logging.DEBUG)

# Read the config file
if not os.path.isfile(os.path.join(sys.path[0], "copykun.cfg")):
	logger.critical("Configuration file not found: copykun.cfg")
	exit(1)	
config = configparser.ConfigParser()
with codecs.open(os.path.join(sys.path[0], "copykun.cfg"), "r", "utf8") as f:
	config.read_file(f)

database = SqliteDatabase(os.path.join(sys.path[0], config.get("Database", "db_name")))

class BaseModel(Model):
	class Meta:
		database = database
		
'''
A reddit submission which contains a link to content to copy
'''
class Post(BaseModel):
	id = CharField(primary_key = True)
	
'''
Reddit content copied by the bot
'''
class Content(BaseModel):
	permalink = CharField()
	created = IntegerField()
	edited = IntegerField(null = True)
	last_checked = IntegerField()
	update_interval = IntegerField()
	post = ForeignKeyField(Post, related_name = "content", null = True)
	
'''
A reply posted by the bot
'''
class Reply(BaseModel):
	permalink = CharField()
	latest_content = TextField()
	post = ForeignKeyField(Post, related_name = "replies")
	
'''
An edit made to a reply by the bot
'''
class Edit(BaseModel):
	content = TextField()
	edit_time = IntegerField()
	post = ForeignKeyField(Post, related_name = "edits")
	
class Database(object):
	
	def __init__(self):	
		database.connect()
		
	def __del__(self):
		database.close()
	
	def create_tables(self):
		database.create_tables([Post, Content, Reply, Edit])
		
	def convert_json(self):
		if not os.path.isfile(os.path.join(sys.path[0], "replied_posts.json")):
			return
			
		with open(os.path.join(sys.path[0], "replied_posts.json"), "r") as f:
			replied_posts = json.load(f)
		for id in replied_posts:
			if Post.select().where(Post.id == id).exists():
				continue
			print(id)
			replied_post = replied_posts[id]
			with database.transaction():
				post = Post.create(
					id = id
				)
				content = Content.create(
					permalink = replied_post["permalink"],
					created = replied_post["created_utc"],
					edited = None if not replied_post["edited"] else replied_post["edited"],
					last_checked = replied_post["last_checked"],
					update_interval = replied_post["update_interval"],
					post = post
				)
				reply = Reply.create(
					permalink = replied_post["reply"],
					latest_content = replied_post["latest_content"],
					post = post
				)
				for edit in replied_post["edit_history"]:
					edit = Edit.create(
						content = edit,
						# ex: "Edited @ 09/03/2016 17:00:36"
						edit_time = datetime.strptime(edit[9:28], "%d/%m/%Y %H:%M:%S").replace(tzinfo = timezone.utc).timestamp(),
						post = post
					)

	'''
	Check if a given post exists in the database
	'''
	def is_post_in_db(self, post_id):
		return Post.select().where(Post.id == post_id).exists()		
	
	'''
	Retrieve an interator of Posts which need to be checked for edits
	'''
	def get_posts_to_check_edits(self):
		now = datetime.utcnow().timestamp()
		posts = Post.select().join(Content).where(now > Content.last_checked + Content.update_interval)
		return posts
			
	def get_reply_to_post(self, post_id):
		return Reply.select().join(Post).where(Post.id == post_id).first()
	
	def save_objects(self, objs):
		with database.transaction():
			for obj in objs:
				obj.save()
