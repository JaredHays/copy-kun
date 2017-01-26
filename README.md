# copy-kun

Copy-kun is a Reddit bot that monitors a subreddit for posts that link to other Reddit content and comments with a copy of that content.

Copy-kun features:
  * Automatically copy any reddit post or comment linked
  * For comments, build a full comment tree from the root post to the linked comment
  * Monitor content for edits and update copies with changes (displayed as a diff)
  * Optional: respond to summons (via username mention) to reply with a copy of a link's content
  * Optional: include boilerplate and/or taglines in a header or footer
  * Optional: forward messages received by the bot to another account
  

# Requirements

Copy-kun depends on PRAW for reddit requests and peewee for database functionality

 * [PRAW GitHub Link](https://github.com/praw-dev/praw)

 * [PRAW Documentation](https://praw.readthedocs.io/en/v3.4.0/index.html)

 * [Peewee GitHub Link](https://github.com/coleifer/peewee)

 * [Pewee Docs](http://docs.peewee-orm.com/en/latest)
 
# OAuth

To get your refresh token you will need to request it from the reddit site, read the praw oauth section and use thier [Refresh Token Script](http://praw.readthedocs.io/en/latest/tutorials/refresh_token.html#refresh-token) 

# Setup

Edit copykun_sample.cfg with your bot's account details and options, and rename the file "copykun.cfg".
