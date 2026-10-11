import sqlite3
import httpx
from resip.marzban import Marzban
from dataclasses import replace


def test_user_identity_from_real_readonly_sqlite_schema(env,tmp_path):
 settings,_,_=env
 db=tmp_path/'marzban.db'
 with sqlite3.connect(db) as c:
  c.execute('CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE, status TEXT)')
  c.execute('INSERT INTO users (id, username, status) VALUES (42, "alice", "active")')
 settings=replace(settings, marzban_db_url=f'sqlite:///file:{db}?mode=ro&uri=true')
 def handler(req):
  if req.url.path=='/api/admin/token':
   return httpx.Response(200,json={'access_token':'example'})
  if req.url.path=='/api/user/alice':
   # THIS MATCHES MARZBAN PUBLIC RESPONSE: user ID IS NOT PRESENT.
   return httpx.Response(200,json={'username':'alice','status':'active'})
  return httpx.Response(404)
 m=Marzban(settings,transport=httpx.MockTransport(handler))
 assert m.user('alice')['id']==42
 m.close()
