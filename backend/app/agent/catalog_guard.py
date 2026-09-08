"""Does an answer name products the tenant actually sells?

The catalogue said "Leche"; the agent offered "Leche pausterizada". Prompt
rule 1 already forbade inventing a product and did not stop it, because a name
that is *almost* right does not feel invented to a model. It is one all the
same: the customer arrives asking for something the business does not sell,
and somebody has to explain that the agent made it up.

**One detector, two callers.** `AgentCore` runs it over a reply before that
reply leaves, against the tenant's live catalogue; the golden set runs it over
a model's answer, against the demo fixture. Both import this module. Two
copies would drift, and the copy that drifted would be the one guarding real
customers - so the catalogue names are a *parameter* here and this file has no
idea where they came from. It reads no database and imports nothing from
`evals/`.

**A false alarm is the expensive failure.** A guard that fires on a correct
answer replaces it with an apology, and teaches whoever maintains it to switch
the guard off - which is worth less than no guard at all. Everything below is
shaped by that. The detector only ever reports a phrase that is *anchored* in
the tenant's own catalogue wording, or - when the caller can supply a
vocabulary to judge against - a name the model capitalised out of nowhere.
Prose that merely talks about a product is not a rename.

**The known limit.** A wholly invented name written in lower case -
"tenemos sushi variado" - is not detected. Catching it would mean flagging any
lower-case word the tenant never wrote, and in Spanish prose that is most
words: "pedido", "delivery", "promoción", "rápido". Every one of those would
be a false alarm on a correct answer, which is the failure this file is built
to avoid. The expensive half of that case is caught structurally elsewhere -
an invented *price* with no denial - and an honest gap beats a guard nobody
trusts.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

_WORD = re.compile(r"[0-9a-záéíóúüñA-ZÁÉÍÓÚÜÑ]+")

# What ends a sentence, and therefore licenses the capital letter that starts
# the next one. A capital there says nothing about being a product name.
_SENTENCE_OPENERS = set(".!?¡¿:;\n•*-–—\"'()[]")

# Below this a word is a function word, a currency mark or a unit, never a
# descriptor bolted onto a product name. It is a structural filter rather than
# a list: almost every Spanish word that carries a sentence on - "de", "con",
# "es", "hay", "ya", "sin", "por", "más" - is three characters or fewer, and
# no descriptor that renames a product is. It costs the odd three-letter
# adjective, which is the cheap direction to err in.
_MIN_DESCRIPTOR_CHARS = 4

# Endings that belong to a verb and not to a descriptor. `-an` and `-en` are
# the third-person plural of every Spanish conjugation - "vienen", "cuestan",
# "incluyen", "llevan" - which excludes an entire open class of verbs without
# naming them one by one. Gerunds and infinitive-plus-clitic forms never
# rename anything either. The price is a descriptor like "virgen" or "joven";
# a miss, not a false alarm, and that is the direction this file errs in.
_VERB_ENDINGS = ("an", "en", "ando", "iendo", "arse", "erse", "irse", "aron", "ieron")

# Words that carry the sentence on instead of continuing a product name.
#
# This replaced a whitelist of adjective endings, and the whitelist was the
# leak: `-ada` caught "helada" and "pausterizada" and missed "fresca",
# "entera" and "light", which are the same bug wearing a different suffix.
# Inverting it works because the two sides are not symmetric. The words that
# follow a product name *in prose* are closed classes - articles, prepositions,
# conjunctions, pronouns, quantifiers - plus the handful of verbs a business
# uses to talk about what it sells. Spanish adjectives are an open class and
# cannot be enumerated; these can.
#
# Words of three characters or fewer are listed only where they read oddly by
# their absence: `_MIN_DESCRIPTOR_CHARS` already excludes every one of them.
_CARRIES_THE_SENTENCE_ON = frozenset(
    (
        # determiners and possessives
        "el", "la", "los", "las", "un", "una", "unos", "unas", "lo", "al", "del",
        "este", "esta", "estos", "estas", "ese", "esa", "esos", "esas",
        "aquel", "aquella", "aquellos", "aquellas",
        "mi", "mis", "tu", "tus", "su", "sus",
        "nuestro", "nuestra", "nuestros", "nuestras",
        "otro", "otra", "otros", "otras", "cada",
        "todo", "toda", "todos", "todas",
        "mismo", "misma", "mismos", "mismas", "cualquier", "cualquiera",
        "algun", "alguna", "algunos", "algunas",
        "ningun", "ninguna", "ningunos", "ningunas",
        # prepositions
        "ante", "bajo", "con", "contra", "desde", "durante", "entre", "hacia",
        "hasta", "mediante", "para", "por", "segun", "sin", "sobre", "tras",
        # conjunctions and relatives
        "pero", "sino", "aunque", "porque", "pues", "que", "como", "cuando",
        "donde", "mientras", "quien", "quienes", "cual", "cuales",
        "cuyo", "cuya", "cuanto", "cuanta", "cuantos", "cuantas",
        # pronouns
        "ella", "ellos", "ellas", "usted", "ustedes", "nosotros",
        "alguien", "algo", "nada", "nadie",
        # adverbs and quantifiers
        "tambien", "tampoco", "solo", "solamente", "muy", "mucho", "mucha",
        "muchos", "muchas", "poco", "poca", "pocos", "pocas", "menos",
        "bien", "ahora", "manana", "ayer", "siempre", "nunca", "jamas",
        "quiza", "quizas", "asi", "todavia", "recien", "casi", "apenas",
        "incluso", "ademas", "entonces", "luego", "despues", "antes",
        "pronto", "tarde", "temprano", "aqui", "alli", "alla", "arriba",
        "abajo", "cerca", "lejos", "gratis", "igual", "iguales", "actual",
        "total", "ideal", "tales", "primero", "segundo", "ultimo", "ultima",
        # the verbs a business uses to talk about what it sells, in the
        # persons a chat reply actually uses
        "era", "eran", "fue", "fueron", "sera", "seran", "seria", "serian",
        "esta", "estaba", "estuvo", "estara", "estamos",
        "habia", "habra", "hubo",
        "tengo", "tienes", "tiene", "tenemos", "tenia", "tendra",
        "viene", "venia", "vendra",
        "lleva", "llevamos", "llevaba",
        "incluye", "incluimos", "contiene",
        "cuesta", "costaba", "costara", "vale", "valia",
        "sale", "salia", "queda", "quedaba",
        "sirve", "servimos", "puede", "podemos", "podria",
        "trae", "traemos", "hace", "hacemos", "vamos",
        "suele", "demora", "dura", "requiere", "necesita", "necesitas",
        "ofrece", "ofrecemos", "maneja", "manejamos",
        "vende", "vendemos", "prepara", "preparamos",
        "trabaja", "trabajamos", "cuenta", "contamos", "acompana",
        "pesa", "mide", "rinde",
        # participles and states that describe a product's availability
        # without renaming it
        "disponible", "disponibles", "agotado", "agotada", "agotados",
        "agotadas", "incluido", "incluida", "incluidos", "incluidas",
        "listo", "lista", "listos", "listas",
    )
)  # fmt: skip


@dataclass(frozen=True)
class _Token:
    raw: str
    norm: str
    # True when punctuation, not just a space, separates this token from the
    # one before it. A name does not survive a comma.
    after_punctuation: bool
    starts_a_sentence: bool


def fold(text: str) -> str:
    """Lowercase and accent-free, so `Ceviche` and `ceviché` are one word."""
    lowered = unicodedata.normalize("NFD", text.lower())
    return "".join(char for char in lowered if unicodedata.category(char) != "Mn")


def words(text: str) -> list[str]:
    """The folded words of `text`, punctuation discarded."""
    return [fold(match.group()) for match in _WORD.finditer(text)]


def _tokenise(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    previous_end = 0
    for index, match in enumerate(_WORD.finditer(text)):
        gap = text[previous_end : match.start()]
        tokens.append(
            _Token(
                raw=match.group(),
                norm=fold(match.group()),
                after_punctuation=any(not char.isspace() for char in gap),
                starts_a_sentence=index == 0 or any(char in _SENTENCE_OPENERS for char in gap),
            )
        )
        previous_end = match.end()
    return tokens


def _is_a_descriptor(word: str) -> bool:
    """Whether `word`, sitting right after a name, renames what it follows.

    "Leche" + "pausterizada" is a product the business does not sell. "Leche"
    + "cuesta" is a sentence about one it does.
    """
    return (
        len(word) >= _MIN_DESCRIPTOR_CHARS
        and not word[0].isdigit()
        and word not in _CARRIES_THE_SENTENCE_ON
        and not word.endswith(_VERB_ENDINGS)
    )


@dataclass(frozen=True)
class CatalogueGuard:
    """One tenant's catalogue, ready to judge an answer against.

    Built once and reused: `AgentCore` checks a reply and then, if it retried,
    the retried reply, and both go through the same instance so the catalogue
    is read once per turn.
    """

    # The names as whoever owns them wrote them. Kept so a caller can quote
    # them back to the model verbatim in a correction.
    catalogue: tuple[str, ...]
    # The same names, folded into words, which is what matching runs on.
    folded: tuple[tuple[str, ...], ...]
    # Every word the tenant is known to have written. Empty means "unknown",
    # which switches the introduced-name check off - see `_is_introduced_name`.
    vocabulary: frozenset[str]

    @property
    def heads(self) -> frozenset[str]:
        return frozenset(name[0] for name in self.folded)

    def invented_names(self, text: str) -> list[str]:
        """Product names in `text` that the catalogue does not contain.

        Two shapes, because both reached a real customer:

        * a catalogue name with a descriptor bolted on - the catalogue said
          "Leche", the agent offered "Leche pausterizada";
        * a name the model introduced on its own - a capitalised word from
          nowhere in the tenant's data, which is how sushi appears on a
          criollo menu. Only when a vocabulary was supplied.

        A guard with no names to check against reports nothing rather than
        everything: an empty catalogue means the answer cannot be judged, not
        that every word in it is invented.
        """
        if not self.folded:
            return []

        tokens = _tokenise(text)
        heads = self.heads
        invented: list[str] = []

        for index, token in enumerate(tokens):
            if token.norm in heads:
                claimed = self._claimed_from_catalogue_name(tokens, index)
            elif self._is_introduced_name(token):
                claimed = self._claimed_from_a_new_name(tokens, index)
            else:
                continue
            if claimed and not self._is_part_of_a_catalogue_name(claimed):
                invented.append(" ".join(claimed))

        return invented

    # ------------------------------------------------------------- internals

    def _claimed_from_catalogue_name(self, tokens: list[_Token], index: int) -> tuple[str, ...]:
        """The catalogue name at `index`, plus a descriptor if one was added.

        The longest catalogue name that matches wins, so "Inca Kola 500ml"
        is read whole rather than as "Inca Kola" followed by a stray "500ml".
        """
        matched: tuple[str, ...] = ()
        for name in self.folded:
            for length in range(len(name), 0, -1):
                run = tuple(token.norm for token in tokens[index : index + length])
                if run == name[:length]:
                    if length > len(matched):
                        matched = run
                    break
        if not matched:  # pragma: no cover - `index` is only reached via `heads`
            return ()

        following = index + len(matched)
        if following >= len(tokens):
            return ()
        extra = tokens[following]
        if extra.after_punctuation or not _is_a_descriptor(extra.norm):
            return ()
        return (*matched, extra.norm)

    def _is_introduced_name(self, token: _Token) -> bool:
        """A capitalised word the tenant never wrote, mid-sentence.

        Off entirely when no vocabulary was supplied. At runtime the agent
        knows the catalogue and nothing else, so every capitalised word the
        model wrote - "Miraflores", "Yape", the customer's own name - would
        look introduced, and the guard would fire on correct answers all day.
        The golden set holds the whole fixture and can afford the check.

        Sentence-initial capitals say nothing, numbers say nothing, and a
        single letter is the `S` of `S/`.
        """
        return (
            bool(self.vocabulary)
            and not token.starts_a_sentence
            and token.raw[:1].isupper()
            and len(token.raw) >= 3
            and not token.raw.isdigit()
            and token.norm not in self.vocabulary
        )

    def _claimed_from_a_new_name(self, tokens: list[_Token], index: int) -> tuple[str, ...]:
        """The whole phrase the model introduced, up to where prose resumes.

        It carries on through the words a name is made of - more capitals, or
        a descriptor - and stops at the first word that is neither.
        """
        claimed = [tokens[index].norm]
        for token in tokens[index + 1 : index + 4]:
            continues = token.raw[:1].isupper() or _is_a_descriptor(token.norm)
            if token.after_punctuation or not continues:
                break
            claimed.append(token.norm)
        return tuple(claimed)

    def _is_part_of_a_catalogue_name(self, phrase: tuple[str, ...]) -> bool:
        """Whether the phrase is the catalogue's own wording, whole or short.

        "Inca Kola" for "Inca Kola 500ml" is somebody being brief, not
        somebody inventing. "Inca Kola fria" is a different product.
        """
        return any(
            name[start : start + len(phrase)] == phrase
            for name in self.folded
            for start in range(len(name) - len(phrase) + 1)
        )


def build_catalogue_guard(
    names: Iterable[str], *, vocabulary: Iterable[str] = ()
) -> CatalogueGuard:
    """A guard for one catalogue.

    `names` are product names exactly as the tenant wrote them.

    `vocabulary` is everything the tenant is known to have written anywhere -
    names, categories, descriptions, uploaded documents - and switches on the
    introduced-name check. Whole sentences are as welcome as single words:
    they are split here, because a caller that handed one over intact would
    otherwise build a vocabulary of one enormous "word" that matches nothing,
    and the guard would then flag every proper noun on a correct answer.
    Callers that cannot supply a complete vocabulary must leave it out - a
    partial one turns correct answers into alarms.
    """
    catalogue = tuple(name for name in names if name and name.strip())
    folded = tuple(word_list for name in catalogue if (word_list := tuple(words(name))))
    return CatalogueGuard(
        catalogue=catalogue,
        folded=folded,
        vocabulary=frozenset(word for text in vocabulary for word in words(text)),
    )


__all__ = ["CatalogueGuard", "build_catalogue_guard", "fold", "words"]
