import httpx
from sqlalchemy import create_engine, text



class Marzban:
    def __init__(self, settings, transport=None):
        self.s = settings
        self.client = httpx.Client(base_url=settings.marzban_url, timeout=15, transport=transport,
                                   follow_redirects=False)
        self.token = None
        # Marzban's public UserResponse intentionally does not expose DB user.id,
        # although Xray identifies a connection as id.username.
        # Use a strictly read-only DB account / SQLite URI for this single lookup.
        self.engine = None

    def close(self):
        self.client.close()
        if self.engine is not None:
            self.engine.dispose()

    def user_id(self, username):
        if self.engine is None:
            self.engine = create_engine(self.s.marzban_db_url, pool_pre_ping=True)
        with self.engine.connect() as conn:
            user_id = conn.execute(text("SELECT id FROM users WHERE username = :name"), {"name": username}).scalar_one_or_none()
        if user_id is None or not isinstance(user_id, int):
            raise ValueError("Marzban user database identity missing")
        return user_id

    def _auth(self):
        result = self.client.post("/api/admin/token", data={
            "username": self.s.marzban_user, "password": self.s.marzban_password
        })
        result.raise_for_status()
        self.token = result.json()["access_token"]

    def request(self, method, path, **kwargs):
        if not self.token:
            self._auth()
        kwargs["headers"] = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {self.token}"}
        response = self.client.request(method, path, **kwargs)
        if response.status_code == 401:
            self._auth()
            kwargs["headers"]["Authorization"] = f"Bearer {self.token}"
            response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    def user(self, username):
        result = self.request("GET", "/api/user/" + username)
        if result.get("username") != username:
            raise ValueError("Unexpected Marzban user identity")
        # Fetch the server's actual internal identity from the same DB it runs.
        # Do not ever synthesize it from an index or a guessed naming scheme.
        result["id"] = self.user_id(username)
        return result

    def config(self):
        return self.request("GET", "/api/core/config")

    def put_config(self, config):
        return self.request("PUT", "/api/core/config", json=config)
