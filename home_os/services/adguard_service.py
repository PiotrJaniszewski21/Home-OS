import httpx


class AdGuardService:
    def __init__(self, url="http://localhost:3000", username="", password=""):
        self.url = url.rstrip("/")
        self.auth = (username, password) if username else None

    def _get(self, path):
        resp = httpx.get(
            f"{self.url}{path}",
            auth=self.auth,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def _post(self, path, data=None):
        resp = httpx.post(
            f"{self.url}{path}",
            json=data,
            auth=self.auth,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def _delete(self, path, data=None):
        resp = httpx.request(
            "DELETE",
            f"{self.url}{path}",
            json=data,
            auth=self.auth,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    # Stats
    def get_status(self):
        return self._get("/control/status")

    def get_stats(self):
        return self._get("/control/stats")

    def get_query_log(self, limit=50):
        return self._get(f"/control/querylog?limit={limit}")

    # Protection toggle
    def set_protection(self, enabled):
        self._post("/control/protection", {"enabled": enabled})

    # Filtering
    def get_filtering_status(self):
        return self._get("/control/filtering/status")

    def add_filter_url(self, name, url, enabled=True):
        self._post("/control/filtering/add_url", {"name": name, "url": url, "enabled": enabled})

    def remove_filter_url(self, url):
        self._post("/control/filtering/remove_url", {"url": url})

    def refresh_filters(self):
        self._post("/control/filtering/refresh", {"whitelist": False})

    # Custom rules
    def get_custom_rules(self):
        resp = httpx.get(f"{self.url}/control/filtering/rules", auth=self.auth, timeout=10)
        resp.raise_for_status()
        return resp.text

    def set_custom_rules(self, rules):
        httpx.post(
            f"{self.url}/control/filtering/rules",
            content=rules,
            auth=self.auth,
            headers={"Content-Type": "text/plain"},
            timeout=10,
        )

    # DNS rewrites (local DNS records)
    def get_rewrites(self):
        return self._get("/control/rewrite/list")

    def add_rewrite(self, domain, answer):
        self._post("/control/rewrite/add", {"domain": domain, "answer": answer})

    def delete_rewrite(self, domain, answer):
        self._post("/control/rewrite/delete", {"domain": domain, "answer": answer})

    # Clients
    def get_clients(self):
        return self._get("/control/clients")

    # Top stats
    def get_top_clients(self):
        stats = self.get_stats()
        return {
            "top_queried": stats.get("top_queried_domains", []),
            "top_blocked": stats.get("top_blocked_domains", []),
            "top_clients": stats.get("top_clients", []),
            "num_dns_queries": stats.get("num_dns_queries", 0),
            "num_blocked": stats.get("num_blocked_filtering", 0),
            "avg_processing_time": stats.get("avg_processing_time", 0),
        }
