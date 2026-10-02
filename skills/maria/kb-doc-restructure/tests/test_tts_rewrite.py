#!/usr/bin/env python3
"""Unit tests for tts_rewrite (MAR-1140). Stdlib only — run with:

python3 -m unittest discover -s <skill>/tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tts_rewrite import (  # noqa: E402
    email_to_words,
    number_to_words,
    phone_to_words,
    rewrite_text,
    url_to_words,
)


class TestNumberToWords(unittest.TestCase):
    def test_digits_and_teens(self):
        self.assertEqual(number_to_words(0), "cero")
        self.assertEqual(number_to_words(9), "nueve")
        self.assertEqual(number_to_words(15), "quince")
        self.assertEqual(number_to_words(16), "dieciséis")

    def test_twenties(self):
        self.assertEqual(number_to_words(20), "veinte")
        self.assertEqual(number_to_words(23), "veintitrés")

    def test_tens(self):
        self.assertEqual(number_to_words(40), "cuarenta")
        self.assertEqual(number_to_words(34), "treinta y cuatro")
        self.assertEqual(number_to_words(94), "noventa y cuatro")

    def test_hundreds(self):
        self.assertEqual(number_to_words(100), "cien")
        self.assertEqual(number_to_words(101), "ciento uno")
        self.assertEqual(number_to_words(351), "trescientos cincuenta y uno")

    def test_out_of_range(self):
        with self.assertRaises(ValueError):
            number_to_words(1000)


class TestPhoneToWords(unittest.TestCase):
    def test_plain_9_digits(self):
        # the canonical MAR-1140 example
        self.assertEqual(
            phone_to_words("928353535"),
            "nueve dos ocho treinta y cinco treinta y cinco treinta y cinco",
        )

    def test_country_code(self):
        self.assertEqual(
            phone_to_words("+34928353535"),
            "más treinta y cuatro, nueve dos ocho treinta y cinco treinta y cinco treinta y cinco",
        )

    def test_grouped_with_spaces(self):
        self.assertEqual(
            phone_to_words("928 35 35 35"),
            "nueve dos ocho treinta y cinco treinta y cinco treinta y cinco",
        )

    def test_pair_with_leading_zero(self):
        # 600 07 12 34 -> head digit-by-digit, "07" -> "cero siete"
        self.assertEqual(
            phone_to_words("600071234"),
            "seis cero cero cero siete doce treinta y cuatro",
        )

    def test_extension(self):
        self.assertEqual(
            phone_to_words("928353535 ext. 23"),
            "nueve dos ocho treinta y cinco treinta y cinco treinta y cinco, extensión dos tres",
        )

    def test_extension_word_form(self):
        self.assertEqual(
            phone_to_words("928353535 extensión 105"),
            "nueve dos ocho treinta y cinco treinta y cinco treinta y cinco, extensión uno cero cinco",
        )

    def test_three_digit_country_code(self):
        self.assertEqual(
            phone_to_words("+351912345678"),
            "más trescientos cincuenta y uno, nueve uno dos treinta y cuatro cincuenta y seis setenta y ocho",
        )


class TestEmailToWords(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(
            email_to_words("john.doe@acme.com"),
            "john punto doe arroba acme punto com",
        )

    def test_underscore_and_trailing_punct(self):
        self.assertEqual(
            email_to_words("a_b@acme.com."),
            "a barra baja b arroba acme punto com",
        )


class TestUrlToWords(unittest.TestCase):
    def test_www_with_path_underscore(self):
        self.assertEqual(
            url_to_words("www.acme.com/mi_pagina"),
            "doble uve doble uve doble uve punto acme punto com barra mi barra baja pagina",
        )

    def test_strips_protocol(self):
        self.assertEqual(
            url_to_words("https://acme.com/info"),
            "acme punto com barra info",
        )


class TestRewriteText(unittest.TestCase):
    def test_bakes_all_kinds(self):
        src = "Llama al 928353535 o al +34928353535. Escribe a john.doe@acme.com o visita www.acme.com/mi_pagina."
        out = rewrite_text(src)
        self.assertIn("nueve dos ocho treinta y cinco treinta y cinco treinta y cinco", out)
        self.assertIn("más treinta y cuatro,", out)
        self.assertIn("john punto doe arroba acme punto com", out)
        self.assertIn("doble uve doble uve doble uve punto acme", out)
        # no raw tokens survive
        self.assertNotIn("928353535", out)
        self.assertNotIn("@acme", out)

    def test_leaves_prices_alone(self):
        src = "El precio es 40 € y el descuento 10%."
        self.assertEqual(rewrite_text(src), src)


if __name__ == "__main__":
    unittest.main()
