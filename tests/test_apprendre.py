import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import apprendre as a  # noqa: E402


def faux_fetch(url, token=None):
    if "api.github.com/users" in url:
        return ('[{"name":"musicpro","default_branch":"main"},{"name":"vieux","fork":true},'
                '{"name":"secret","private":true},{"name":"sans-fichier","default_branch":"main"},'
                '{"name":"kidasniger","default_branch":"main"}]')
    if url.endswith("/musicpro/main/kidas.md"):
        return "Lecteur musical Android."
    if url.endswith("/kidasniger/main/README.md"):
        return "Salut, je suis Aboubacar."
    return None


class TestApprendre(unittest.TestCase):
    def test_projets(self):
        txt, nd, nb = a.construire_projets(faux_fetch)
        self.assertEqual((nd, nb), (3, 2))  # fork et privé écartés ; musicpro + profil lus
        self.assertIn("Lecteur musical Android.", txt)
        self.assertIn("Salut, je suis Aboubacar.", txt)
        self.assertIn("sans-fichier", txt)
        self.assertNotIn("secret", txt)

    def test_reseau_en_panne(self):
        self.assertEqual(a.construire_projets(lambda u, t=None: None)[1], 0)

    def test_performance(self):
        posts = [{"texte": "a", "likes": 1, "commentaires": 0, "partages": 0, "type": "x", "date": "2026-11-03T19:35:37+0000"},
                 {"texte": "b", "likes": 5, "commentaires": 1, "partages": 2, "type": "y", "date": "2026-11-04T10:00:00+0000"}]
        txt = a.construire_performance(posts)
        self.assertIn("score 18", txt)
        self.assertEqual(a.construire_performance([]).count("Aucune donnée"), 1)


if __name__ == "__main__":
    unittest.main()
