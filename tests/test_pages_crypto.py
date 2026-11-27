import base64
import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import pages_crypto as pc

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    CRYPTO = True
except ImportError:
    CRYPTO = False

JETON = "EAAB-jeton-court-SECRET-123"


def pem_prive(cle):
    return cle.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


def chiffrer_comme_l_appli(clair, pub, alg=pc.ALG, version=1):
    """Même algorithme que lib/core/crypto_box.dart."""
    cle, iv = os.urandom(32), os.urandom(12)
    sortie = AESGCM(cle).encrypt(iv, clair.encode(), None)
    ct, tag = sortie[:-16], sortie[-16:]
    ek = pub.encrypt(cle, padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                                       algorithm=hashes.SHA256(), label=None))
    b = lambda x: base64.b64encode(x).decode()
    return json.dumps({"v": version, "alg": alg, "ek": b(ek), "iv": b(iv), "ct": b(ct), "tag": b(tag)})


@unittest.skipUnless(CRYPTO, "cryptography absent")
class TestDechiffrementApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cle = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.autre = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def test_aller_retour(self):
        enc = chiffrer_comme_l_appli(JETON, self.cle.public_key())
        self.assertEqual(pc.dechiffrer_jeton(enc, self.cle), JETON)

    def test_espaces_autour_retires(self):
        enc = chiffrer_comme_l_appli("  " + JETON + "\n", self.cle.public_key())
        self.assertEqual(pc.dechiffrer_jeton(enc, self.cle), JETON)

    def test_mauvaise_cle(self):
        enc = chiffrer_comme_l_appli(JETON, self.cle.public_key())
        with self.assertRaises(pc.ErreurChiffrement) as ctx:
            pc.dechiffrer_jeton(enc, self.autre)
        self.assertNotIn(JETON, str(ctx.exception))

    def test_alteration_ct_ou_tag(self):
        doc = json.loads(chiffrer_comme_l_appli(JETON, self.cle.public_key()))
        for champ in ("ct", "tag"):
            copie = dict(doc)
            octets = bytearray(base64.b64decode(copie[champ]))
            octets[0] ^= 1
            copie[champ] = base64.b64encode(bytes(octets)).decode()
            with self.assertRaises(pc.ErreurChiffrement):
                pc.dechiffrer_jeton(json.dumps(copie), self.cle)

    def test_formats_invalides(self):
        bon = json.loads(chiffrer_comme_l_appli(JETON, self.cle.public_key()))
        cas = [None, "", "pas du json " + JETON, "[1]", "{}", "x" * (pc.TAILLE_MAX_ENC + 1)]
        for brut in cas:
            with self.assertRaises(pc.ErreurChiffrement):
                pc.dechiffrer_jeton(brut, self.cle)
        for champ, valeur in (("v", 2), ("alg", "AUTRE"), ("ek", "!!!"), ("iv", "AAAA"),
                              ("tag", "AAAA"), ("ct", ""), ("ct", None)):
            copie = dict(bon)
            copie[champ] = valeur
            with self.assertRaises(pc.ErreurChiffrement, msg=champ):
                pc.dechiffrer_jeton(json.dumps(copie), self.cle)
        copie = dict(bon)
        del copie["tag"]
        with self.assertRaises(pc.ErreurChiffrement):
            pc.dechiffrer_jeton(json.dumps(copie), self.cle)

    def test_jeton_avec_espace_interne_refuse(self):
        enc = chiffrer_comme_l_appli("abc def", self.cle.public_key())
        with self.assertRaises(pc.ErreurChiffrement):
            pc.dechiffrer_jeton(enc, self.cle)

    def test_jeton_vide_refuse(self):
        enc = chiffrer_comme_l_appli("   ", self.cle.public_key())
        with self.assertRaises(pc.ErreurChiffrement):
            pc.dechiffrer_jeton(enc, self.cle)


@unittest.skipUnless(CRYPTO, "cryptography absent")
class TestChargementCle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cle = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.pem = pem_prive(cls.cle)

    def test_pem_brut(self):
        self.assertEqual(pc.charger_cle_privee(self.pem).key_size, 2048)

    def test_pem_avec_retours_litteraux(self):
        self.assertEqual(pc.charger_cle_privee(self.pem.replace("\n", "\\n")).key_size, 2048)

    def test_pem_en_base64(self):
        enc = base64.b64encode(self.pem.encode()).decode()
        self.assertEqual(pc.charger_cle_privee(enc).key_size, 2048)

    def test_refus_sans_fuite(self):
        for brut in (None, "", "   ", "SECRET-pas-une-cle", "-----BEGIN PRIVATE KEY-----\nSECRET\n-----END PRIVATE KEY-----"):
            with self.assertRaises(pc.ErreurChiffrement) as ctx:
                pc.charger_cle_privee(brut)
            self.assertNotIn("SECRET", str(ctx.exception))

    def test_cle_trop_courte_refusee(self):
        try:
            courte = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        except Exception:
            self.skipTest("génération 1024 bits non permise")
        with self.assertRaises(pc.ErreurChiffrement):
            pc.charger_cle_privee(pem_prive(courte))


@unittest.skipUnless(CRYPTO, "cryptography absent")
class TestStockage(unittest.TestCase):
    def setUp(self):
        self.cle = pc.charger_cle_stockage(base64.b64encode(os.urandom(32)).decode())

    def test_aller_retour(self):
        enc = pc.chiffrer_stockage(JETON, self.cle, "u1", "p1")
        self.assertTrue(enc.startswith("v1."))
        self.assertNotIn(JETON, enc)
        self.assertEqual(pc.dechiffrer_stockage(enc, self.cle, "u1", "p1"), JETON)

    def test_nonce_different_a_chaque_fois(self):
        self.assertNotEqual(pc.chiffrer_stockage(JETON, self.cle, "u", "p"),
                            pc.chiffrer_stockage(JETON, self.cle, "u", "p"))

    def test_lie_a_la_page(self):
        enc = pc.chiffrer_stockage(JETON, self.cle, "u1", "p1")
        for uid, pid in (("u1", "p2"), ("u2", "p1")):
            with self.assertRaises(pc.ErreurChiffrement):
                pc.dechiffrer_stockage(enc, self.cle, uid, pid)

    def test_mauvaise_cle_et_alteration(self):
        enc = pc.chiffrer_stockage(JETON, self.cle, "u", "p")
        autre = pc.charger_cle_stockage(base64.b64encode(os.urandom(32)).decode())
        with self.assertRaises(pc.ErreurChiffrement):
            pc.dechiffrer_stockage(enc, autre, "u", "p")
        with self.assertRaises(pc.ErreurChiffrement):
            pc.dechiffrer_stockage(enc[:-4] + "AAAA", self.cle, "u", "p")
        for brut in (None, "", "v2.a.b", "v1.a", "v1.!!!.!!!"):
            with self.assertRaises(pc.ErreurChiffrement):
                pc.dechiffrer_stockage(brut, self.cle, "u", "p")

    def test_cle_stockage_formats(self):
        octets = os.urandom(32)
        self.assertEqual(pc.charger_cle_stockage(base64.b64encode(octets).decode()), octets)
        self.assertEqual(pc.charger_cle_stockage(base64.urlsafe_b64encode(octets).decode()), octets)
        for brut in (None, "", "   ", "AAAA", base64.b64encode(os.urandom(16)).decode(), "SECRET!!!"):
            with self.assertRaises(pc.ErreurChiffrement) as ctx:
                pc.charger_cle_stockage(brut)
            self.assertNotIn("SECRET", str(ctx.exception))

    def test_jeton_vide_refuse(self):
        with self.assertRaises(pc.ErreurChiffrement):
            pc.chiffrer_stockage("", self.cle, "u", "p")


class TestMasque(unittest.TestCase):
    def sortie(self, valeur, env):
        tampon = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), contextlib.redirect_stdout(tampon):
            pc.masquer(valeur)
        return tampon.getvalue()

    def test_actions(self):
        self.assertEqual(self.sortie("abc", {"GITHUB_ACTIONS": "true"}), "::add-mask::abc\n")

    def test_hors_actions_rien(self):
        env = dict(os.environ)
        env.pop("GITHUB_ACTIONS", None)
        tampon = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(tampon):
            pc.masquer("abc")
        self.assertEqual(tampon.getvalue(), "")

    def test_valeurs_non_masquables(self):
        for valeur in ("", None, "a\nb", "a\rb", 5):
            self.assertEqual(self.sortie(valeur, {"GITHUB_ACTIONS": "true"}), "")


if __name__ == "__main__":
    unittest.main()
