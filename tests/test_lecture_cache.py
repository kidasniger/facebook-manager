import unittest
from urllib.parse import urlparse

from scripts import comment_store, lecture_cache

PAGE = "page-1"
GRAPH = "https://g/v"


class FauxGraph:
    """Page Facebook simulée : compte les appels, pagine la liste des posts."""

    def __init__(self, posts, compte_tous_niveaux=True, taille_page=100, refuse_resume=False):
        # posts : {id: {"created": str, "coms": {comment_id: [reponses (ids)]}}}
        self.posts = posts
        self.tous = compte_tous_niveaux
        self.taille = taille_page
        self.refuse = refuse_resume
        self.appels = []

    def total(self, pid):
        coms = self.posts[pid]["coms"]
        return len(coms) + (sum(len(r) for r in coms.values()) if self.tous else 0)

    def __call__(self, url):
        self.appels.append(url)
        chemin = urlparse(url).path.rstrip("/").split("/")
        # url du type https://g/v/<objet>/<edge>?...  -> chemin[-2] = objet, chemin[-1] = edge
        objet, edge = chemin[-2], chemin[-1]
        if objet == PAGE and edge == "posts":
            if self.refuse and "summary" in url:
                return 400, {"error": {"message": "invalid", "code": 100}}
            ids = list(self.posts)
            debut = 0
            if "after=" in url:
                debut = int(url.split("after=")[1].split("&")[0])
            tranche = ids[debut:debut + self.taille]
            data = []
            for pid in tranche:
                p = {"id": pid, "message": "m-" + pid, "created_time": self.posts[pid]["created"]}
                if "summary" in url:
                    p["comments"] = {"summary": {"total_count": self.total(pid)}, "data": []}
                data.append(p)
            res = {"data": data}
            if debut + self.taille < len(ids):
                res["paging"] = {"next": f"{GRAPH}/{PAGE}/posts?after={debut + self.taille}&fields=id,summary"}
            return 200, res
        if edge == "comments" and objet in self.posts:
            return 200, {"data": [{"id": c, "message": "x", "created_time": "t"} for c in self.posts[objet]["coms"]]}
        if edge == "comments":
            for p in self.posts.values():
                if objet in p["coms"]:
                    return 200, {"data": [{"id": r, "message": "r", "created_time": "t",
                                           "from": {"id": PAGE}} for r in p["coms"][objet]]}
            return 200, {"data": []}
        return 404, {"error": {"message": "inconnu", "code": 803}}


def lecteur(graph, horloge=None, **kw):
    return lecture_cache.LecteurCache(PAGE, "tok", GRAPH, graph, horloge=horloge or (lambda: 1_790_000_000.0),
                                      journal=lambda m: None, **kw)


RECENT = "2099-01-01T00:00:00+0000"
VIEUX = "2000-01-01T00:00:00+0000"


class LectureCacheTests(unittest.TestCase):
    def test_second_cycle_sans_changement_ne_fait_qu_un_appel(self):
        g = FauxGraph({"p1": {"created": RECENT, "coms": {"c1": ["r1"], "c2": []}},
                       "p2": {"created": VIEUX, "coms": {"c3": []}}})
        l = lecteur(g)
        posts = l.lire()
        premier = len(g.appels)
        self.assertGreater(premier, 5)  # balayage complet : tout est lu
        g.appels.clear()
        posts2 = l.lire()
        self.assertEqual(1, len(g.appels))
        self.assertEqual({"relus": 0, "reutilises": 2, "balayage": False}, l.stats)
        self.assertEqual([p["id"] for p in posts], [p["id"] for p in posts2])
        self.assertEqual(3, len(posts2[0]["comments"]["data"]))  # c1, r1, c2

    def test_seul_le_post_modifie_est_relu(self):
        g = FauxGraph({"p1": {"created": RECENT, "coms": {"c1": []}},
                       "p2": {"created": VIEUX, "coms": {"c2": []}}})
        l = lecteur(g)
        l.lire()
        g.posts["p1"]["coms"]["c1"].append("r-nouvelle")  # nouvelle réponse imbriquée
        posts = l.lire()
        self.assertEqual({"relus": 1, "reutilises": 1, "balayage": False}, l.stats)
        ids = [c["id"] for c in posts[0]["comments"]["data"]]
        self.assertIn("r-nouvelle", ids)

    def test_comptage_incomplet_relit_les_posts_recents_a_chaque_cycle(self):
        # Facebook ne compte que le premier niveau : une réponse n'est pas détectée par le total.
        g = FauxGraph({"p1": {"created": RECENT, "coms": {"c1": ["r0"]}}}, compte_tous_niveaux=False)
        l = lecteur(g)
        l.lire()
        g.posts["p1"]["coms"]["c1"].append("r1")
        posts = l.lire()
        self.assertIn("r1", [c["id"] for c in posts[0]["comments"]["data"]])

    def test_vieux_post_non_verifie_attend_le_balayage(self):
        t = [1_790_000_000.0]
        g = FauxGraph({"p1": {"created": VIEUX, "coms": {"c1": ["r0"]}}}, compte_tous_niveaux=False)
        l = lecteur(g, horloge=lambda: t[0], balayage_minutes=20)
        l.lire()
        g.posts["p1"]["coms"]["c1"].append("r1")
        posts = l.lire()  # pas de balayage : le vieux post reste en cache
        self.assertNotIn("r1", [c["id"] for c in posts[0]["comments"]["data"]])
        t[0] += 21 * 60   # le balayage de sécurité le retrouve
        posts = l.lire()
        self.assertTrue(l.stats["balayage"])
        self.assertIn("r1", [c["id"] for c in posts[0]["comments"]["data"]])

    def test_repli_si_facebook_refuse_le_comptage(self):
        g = FauxGraph({"p1": {"created": RECENT, "coms": {"c1": []}}}, refuse_resume=True)
        l = lecteur(g)
        posts = l.lire()
        self.assertFalse(l.resume_ok)
        self.assertEqual(1, len(posts))
        l.lire()
        self.assertEqual(1, l.stats["relus"])  # lecture complète à chaque cycle, comme avant

    def test_cycle_partiel_redonne_les_posts_anciens_depuis_le_cache(self):
        g = FauxGraph({"p1": {"created": RECENT, "coms": {"c1": []}},
                       "p2": {"created": VIEUX, "coms": {"c2": []}}}, taille_page=1)
        l = lecteur(g)
        self.assertEqual(2, len(l.lire()))
        g.appels.clear()
        posts = l.lire()  # 1 seule page listée, mais p2 revient du cache
        self.assertEqual({"p1", "p2"}, {p["id"] for p in posts})
        self.assertEqual(1, len(g.appels))

    def test_erreur_facebook_garde_son_code(self):
        def get_json(url):
            return 400, {"error": {"message": "limite", "code": 80001}}
        with self.assertRaises(comment_store.FacebookError) as ctx:
            lecteur(get_json).lire()
        self.assertEqual(80001, ctx.exception.code)


class CacheCommentairesTests(unittest.TestCase):
    def test_structure_lue_par_l_app(self):
        posts = [{"id": "p1", "message": "Salut", "created_time": "2026-01-02", "comments": {"data": [
            {"id": "c1", "message": "a", "created_time": "t1", "from": {"id": "u1"}, "parent_id": None, "root_id": "c1"},
            {"id": "r1", "message": "b", "created_time": "t2", "from": {"id": PAGE}, "parent_id": "c1", "root_id": "c1"},
            {"id": "c2", "message": "c", "created_time": "t3", "from": {"id": "u2"}, "parent_id": None, "root_id": "c2"},
        ]}}]
        out = lecture_cache.construire_cache_commentaires(posts, {"fan_count": 5}, PAGE, "Q")
        coms = out["posts"][0]["comments"]["data"]
        self.assertEqual(["c1", "c2"], [c["id"] for c in coms])
        self.assertTrue(coms[0]["page_a_repondu"])
        self.assertFalse(coms[1]["page_a_repondu"])
        self.assertEqual("r1", coms[0]["comments"]["data"][0]["id"])
        self.assertEqual({"fan_count": 5}, out["page"])
        self.assertEqual(lecture_cache.sans_horodatage(out), lecture_cache.sans_horodatage(dict(out, updated="Z")))


if __name__ == "__main__":
    unittest.main()
