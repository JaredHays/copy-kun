#!/usr/bin/env python3

import codecs
import configparser
import difflib
import json
import logging
import praw
import pdb
import re
import os
import sys
import time
import traceback
import urllib.parse
from database import *
from datetime import datetime
from random import randint

# Set up logging 
logging.basicConfig(filename = os.path.join(sys.path[0], "copykun.log"), format = "%(asctime)s %(message)s")
logger = logging.getLogger("copykun")
logger.setLevel(logging.INFO)

# Read the config file
if not os.path.isfile(os.path.join(sys.path[0], "copykun.cfg")):
    logger.critical("Configuration file not found: copykun.cfg")
    exit(1) 
config = configparser.ConfigParser(interpolation = configparser.ExtendedInterpolation())
with codecs.open(os.path.join(sys.path[0], "copykun.cfg"), "r", "utf8") as f:
    config.read_file(f)
    
COMMENT_TYPE_PREFIX = "t1_"
# The number of /-separated segments a url with a comment id 
# specified will split into (not counting empties)
URL_LENGTH_WITH_COMMENT = 8

user_agent = (config.get("Reddit", "user_agent"))

reddit = praw.Reddit(user_agent = user_agent)
reddit.login(config.get("Reddit", "username"), config.get("Reddit", "password"), disable_warning = True)
subreddit = reddit.get_subreddit(config.get("Reddit", "subreddit"))

taglines = json.loads(config.get("Reddit", "taglines"), "utf-8") if config.has_option("Reddit", "taglines") else None
forwarding_address = config.get("Reddit", "forwarding_address") if config.has_option("Reddit", "forwarding_address") else ""
summon_phrase = config.get("Reddit", "summon_phrase") if config.has_option("Reddit", "summon_phrase") else ""
footer = config.get("Reddit", "footer") if config.has_option("Reddit", "footer") else ""

link_regex = r"https?://(?:.+\.)?reddit\.com(?P<path>/r/[^?\s]*)(?P<query>\?[\w-]+(?:=[\w-]*)?(?:&[\w-]+(?:=[\w-]*)?)*)?"
short_link_regex = r"https?://redd\.it/(?P<post_id>\w*)"

def copykun_exception_hook(excType, excValue, traceback, logger = logger):
   logger.error("**** EXCEPTION: ", exc_info = (excType, excValue, traceback))

sys.excepthook = copykun_exception_hook

class CannotCopyError(Exception):
    pass

class CopyKun(object):
            
    def __init__(self):     
        self.database = Database()
            
    '''
    Get the correct comment or submission from a permalink since PRAW
    only lets you get submissions
    '''
    def get_correct_reddit_object(self, link):
        match = re.search(link_regex, link, re.IGNORECASE)
        if match:
            orig_url = "https://www.reddit.com" + match.group("path")
        else:
            raise CannotCopyError("Failure parsing link for: \"" + link + "\"")
        try:
            link = reddit.get_submission(orig_url)
            # Check if link is to comment
            if link.comments and link.comments[0]:
                orig_url_split = [x for x in orig_url.split("/") if len(x.strip()) > 0]
                post_url_split = [x for x in link.comments[0].permalink.split("/") if len(x.strip()) > 0]
                # Link is to comment
                if orig_url == link.comments[0].permalink or (len(orig_url_split) == URL_LENGTH_WITH_COMMENT and len(post_url_split) == URL_LENGTH_WITH_COMMENT and orig_url_split[URL_LENGTH_WITH_COMMENT - 1] == post_url_split[URL_LENGTH_WITH_COMMENT - 1]):
                    return link.comments[0]
                # Link is to post
                else:
                    return link
            # Link is to post
            else:
                return link
        # PRAW can throw a KeyError if it can't parse response JSON or a RedirectException for certain non-post reddit links
        except (KeyError, praw.errors.RedirectException) as e:
            raise CannotCopyError("Failure parsing JSON for: \"" + orig_url + "\" (safe to ignore if URL is a non-submission reddit link)")
        except (TypeError, praw.errors.APIException, praw.errors.HTTPException) as e:
            logger.exception("Failure fetching url: \"" + orig_url + "\"")
            return None
            
    ''' 
    Get the post to copy if one is linked in the original post
    '''
    def get_post_to_copy(self, original_post):
        link = None
        post_id = None
        if original_post.author and original_post.author.name == config.get("Reddit", "username"):
            return None
        # Check self text for link to other sub
        if type(original_post) is praw.objects.Comment or original_post.is_self:
            text = original_post.body if type(original_post) is praw.objects.Comment else original_post.selftext 
            # Regular link
            match = re.search(link_regex, text, re.IGNORECASE)
            if match:
                link = match.group(0)
            # Short link
            match = re.search(short_link_regex, text, re.IGNORECASE)
            if match:
                post_id = match.group("post_id")
        # Check url for reddit link 
        elif original_post.domain.endswith("reddit.com"):
            link = original_post.url
        # Check url for shortened link
        elif original_post.domain == "redd.it":
            match = re.search(short_link_regex, original_post.url, re.IGNORECASE)
            if match:
                post_id = match.group(1)
        # Found reddit link
        if link:
            link = urllib.parse.unquote(str(link))
            return self.get_correct_reddit_object(link)
        # Found short link
        elif post_id:
            try:
                # Short links can only be to posts so no comment test
                return reddit.get_submission(submission_id = post_id)
            except (TypeError, praw.errors.APIException) as e:
                logger.exception("Failure fetching short url: \"" + original_post.url + "\"")
                return None
        # Found nothing
        else:
            return None
        
    '''
    Get the text to be copied from a post or comment
    '''
    def get_post_text(self, post):
        submission = post.submission if type(post) is praw.objects.Comment else post
        title = submission.title
        content = ""
        # Copy post content
        if submission.is_self and len(submission.selftext) > 0:
            for para in submission.selftext.split("\n"):
                content += "> " + para + "\n"
        # No content, copy link
        else:
            content += submission.url + "\n"
        # Copy entire comment chain
        if type(post) is praw.objects.Comment:
            try:
                content += self.get_comment_chain(post)
            # Could not find a comment in the chain
            except Exception as e:
                content += "\n\n[Error building full comment tree]\n\n"
                for para in post.body.split("\n"):
                    content += "> " + para + "\n"
                logger.exception("Error building comment tree for \"" + post.id + "\"")
        return title, content
        
    '''
    Build a comment chain
    '''
    def get_comment_chain(self, post):
        submission = post.submission
        op_name = submission.author.name if submission.author else "[deleted]"
        comments = {comment.id: comment for comment in praw.helpers.flatten_tree(submission.comments)}
        comment_id_list = []
        current_id = post.id
        
        # Build comment chain (in reverse)
        fetched_more = False
        while current_id != submission.id:
            if current_id in comments:
                comment_id_list.append(current_id)
                # Slice id to remove type prefix
                current_id = comments[current_id].parent_id[3:]
            # Could not find a comment in the chain, so fetch more comments
            elif not fetched_more:
                submission.replace_more_comments(limit = None)
                comments = {comment.id: comment for comment in praw.helpers.flatten_tree(submission.comments)}
                fetched_more = True
            # Still could not find a comment, so fetch it individually
            else:
                comment_url = submission.permalink + current_id
                comment = self.get_correct_reddit_object(comment_url)
                comments[comment.id] = comment
        content = ""
        level = 2
        for comment_id in comment_id_list[::-1]:
            # Author account exists
            if comments[comment_id].author:
                if comments[comment_id].author.name == op_name:
                    author = "OP"
                else:
                    author = "/u/" + comments[comment_id].author.name
            # Author account deleted
            else:
                author = "[deleted]"
            content += ("> " * level) + author + ":\n\n"
            # Comment body exists
            if comments[comment_id].body:
                for para in comments[comment_id].body.split("\n"):
                    content += (">" * level) + para + "\n"
            # Comment body deleted
            else:
                content += ("> " * level) + "[deleted]\n"
            level += 1
        return content
        
    '''
    Copy the content of a reddit post
    '''
    def copy_post(self, parent, link):
        title, content = self.get_post_text(link)
        if len(content) + len(title) > 0:
            text = ""
            if taglines and len(taglines) > 0:
                text += taglines[randint(0, len(taglines) - 1)] 
            text += "\n\n----\n"
            if title:
                text += title + "\n\n"
            if content:
                text += content
            text += "\n\n----\n\n"
            text += footer
            try:
                if type(parent) is praw.objects.Submission:
                    comment = parent.add_comment(text)
                else:
                    comment = parent.reply(text)
                
                # ID is either post ID or post id + comment ID depending on type
                parent_id = parent.id if type(parent) is praw.objects.Submission else parent.submission.id + "+" + parent.id
                
                db_post = Post.create(id = parent_id)
                db_content = Content()
                db_content.permalink = link.permalink
                db_content.created = link.created_utc
                db_content.edited = None if not link.edited else link.edited
                db_content.last_checked = datetime.utcnow().timestamp()
                db_content.update_interval = 60
                db_content.post = db_post
                db_content.save()
                
                db_reply = Reply()
                db_reply.permalink = comment.permalink
                db_reply.latest_content = content
                db_reply.post = db_post
                db_reply.save()
                
                logger.info("Successfully copied \"" + link.id + "\" to \"" + parent_id + "\"")
            except praw.errors.APIException:
                logger.exception("Failed to copy \"" + link.id + "\" to \"" + parent_id + "\"")

    ''' 
    Check for new posts to copy 
    '''
    def check_new_posts(self):
        for post in subreddit.get_new(limit = 8):
            if not self.database.is_post_in_db(post.id):
                try:
                    link = self.get_post_to_copy(post)
                except CannotCopyError:
                    ignore = Post.create(id = post.id)
                    continue
                # Found post to copy
                if link:
                    self.copy_post(post, link)
                        
    
    '''
    Forward a message from the bot
    '''
    def forward_message(self, message):
        if not forwarding_address:
            return
        subject = "[/u/" + message.author.name + "] " + message.subject
        body = ("https://www.reddit.com" + message.context) if hasattr(message, "context") else ""
        body += "\n\n"
        for para in message.body.split("\n"):
                    body += "> " + para + "\n"
        try:
            reddit.send_message(forwarding_address, subject, body)
            logger.info("Successfully forwarded message from /u/" + message.author.name)
        except praw.errors.APIException:
            logger.exception("Failed to forward message from /u/" + message.author.name)
    
    '''
    Check any messages sent to the bot
    '''
    def check_messages(self):
        for unread in reddit.get_unread(unset_has_mail = True, update_user = True):
            # Respond to summon
            if summon_phrase and (unread.subject.lower().startswith("username mention") or unread.subject.lower().startswith("comment reply")):
                lines = [line for line in unread.body.split("\n") if line]
                if len(lines) >= 2 and lines[0].lower().startswith(summon_phrase):
                    parent = self.get_correct_reddit_object("https://www.reddit.com" + unread.context)
                    if parent.subreddit == subreddit and not self.database.is_post_in_db(parent.submission.id + "+" + parent.id):
                        link = self.get_post_to_copy(parent)
                        self.copy_post(parent, link)
                else:
                    self.forward_message(unread)
            # Forward message
            else:
                self.forward_message(unread)
            unread.mark_as_read()
            
    '''
    Check for posts that have been edited
    '''
    def check_edits(self):
        for db_post in self.database.get_posts_to_check_edits():
            db_content = db_post.content.get()
            rd_content = self.get_correct_reddit_object(db_content.permalink)
            if not rd_content:
                continue
            # Post was edited more recently than last check
            if rd_content.edited and rd_content.edited > db_content.last_checked:
                db_reply = self.database.get_reply_to_post(db_post.id)
                rd_reply = self.get_correct_reddit_object(db_reply.permalink)
                body_start = rd_reply.body.index("----\n") + 5
                body_end = rd_reply.body.rindex("----")
                footer = rd_reply.body[body_end:]
                old_body = rd_reply.body[body_start:body_end]
                # Jump back one more horizontal rule for each previous edit in the post
                for i in range(db_post.edits.count()):
                    try:
                        body_end = old_body.rindex("\n\n----")
                        old_body = old_body[0:body_end]
                    # This can happen if an edit was saved but the reply body was not updated
                    except ValueError:
                        break
                title, content = self.get_post_text(rd_content)
                new_body = content
                # Diff the previous version with the edited version to get the latest changes
                diff = list(difflib.unified_diff(db_reply.latest_content.split("\n"), new_body.split("\n")))
                edit_content = "Edited @ " + datetime.fromtimestamp(rd_content.edited).strftime("%d/%m/%Y %H:%M:%S") + "\n\n"
                # No actual difference, so don't bother editing
                if len(diff) == 0:
                    db_content.last_checked = time.time() 
                    db_content.edited = rd_content.edited
                    db_content.update_interval = min(db_content.update_interval * 2, 16384)
                    self.database.save_objects([db_content])
                    continue
                for line in diff[3:]:
                    # Swap + or - with last > (or ' ' with last > which is harmless)
                    match = re.search(r"([\+|\-| ]>*).*", line, re.IGNORECASE)
                    if match:
                        idx = len(match.group(1))
                        if line[idx:].strip() == "":
                            continue
                        # Escape + and - to avoid reddit turning it into a bullet point
                        line = line[1:idx] + ("\\" if line[0] == "-" or line[0] == "+" else "") + line[0] + " " + line[idx:]
                    edit_content += line + "\n\n"
                    
                text = rd_reply.body[0:body_start] + old_body
                for edit in db_post.edits:
                    text += "\n\n----\n" + edit.content
                text += "\n\n----\n" + edit_content
                text += footer
                try:
                    rd_reply.edit(text)
                    
                    # Update Content object
                    db_content.last_checked = time.time()
                    db_content.edited = rd_content.edited
                    db_content.update_interval = 60
                    
                    # Update Reply object
                    db_reply.latest_content = new_body
                    
                    # Create new Edit object
                    edit = Edit()
                    edit.content = edit_content
                    edit.edit_time = rd_content.edited
                    edit.post = db_post
                    
                    self.database.save_objects([db_content, db_reply, edit])
                    
                    logger.info("Successfully edited \"" + rd_reply.id + "\" in \"" + db_post.id.strip() + "\"")
                except praw.errors.APIException as e:
                    db_content.last_checked = time.time()
                    db_content.edited = rd_content.edited
                    db_content.update_interval = min(db_content.update_interval * 2, 16384 * 2)
                    self.database.save_objects([db_content])
                    logger.exception("Failed to edit \"" + rd_reply.id + "\" in \"" + db_post.id.strip() + "\"")
            # Not edited since last check
            else:
                db_content.last_checked = time.time() 
                db_content.edited = rd_content.edited
                db_content.update_interval = min(db_content.update_interval * 2, 16384 * 2)
                self.database.save_objects([db_content])
    
def main():
    copykun = CopyKun()
    try:
        start = time.time()
        copykun.check_new_posts()
        logger.debug("check_new_posts: {:.2f}s".format(time.time() - start))
        start = time.time()
        copykun.check_messages()
        logger.debug("check_messages: {:.2f}s".format(time.time() - start))
        start = time.time()
        copykun.check_edits()
        logger.debug("check_edits: {:.2f}s".format(time.time() - start))
    except KeyboardInterrupt:
        exit(0)
    except Exception as e:
        logger.exception(e)
        exit(1)
        
if __name__ == "__main__":
    main()
