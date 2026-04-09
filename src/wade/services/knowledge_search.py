"""Boolean search parser and evaluator for knowledge entries.

Supports: AND, OR, NOT, "quoted phrases", parentheses.
Default operator for bare spaces is AND.
All matching is case-insensitive.

Grammar (recursive descent):
    expression = or_expr
    or_expr    = and_expr ("OR" and_expr)*
    and_expr   = not_expr (("AND" | implicit) not_expr)*
    not_expr   = "NOT" primary | primary
    primary    = "(" expression ")" | PHRASE | TERM
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TypeAlias

from pydantic import BaseModel


class TokenKind(StrEnum):
    AND = "AND"
    OR = "OR"
    NOT = "NOT"
    LPAREN = "("
    RPAREN = ")"
    PHRASE = "PHRASE"
    TERM = "TERM"
    EOF = "EOF"


class Token(BaseModel, frozen=True):
    kind: TokenKind
    value: str = ""


# AST nodes


class TermNode(BaseModel, frozen=True):
    """Matches a single term (case-insensitive word boundary)."""

    term: str


class PhraseNode(BaseModel, frozen=True):
    """Matches an exact phrase (case-insensitive)."""

    phrase: str


class AndNode(BaseModel, frozen=True):
    """Both children must match."""

    left: QueryNode
    right: QueryNode


class OrNode(BaseModel, frozen=True):
    """Either child must match."""

    left: QueryNode
    right: QueryNode


class NotNode(BaseModel, frozen=True):
    """Child must NOT match."""

    child: QueryNode


QueryNode: TypeAlias = TermNode | PhraseNode | AndNode | OrNode | NotNode

# Update forward references for recursive models
AndNode.model_rebuild()
OrNode.model_rebuild()
NotNode.model_rebuild()


_KEYWORD_RE = re.compile(r"^(AND|OR|NOT)$", re.IGNORECASE)


def _tokenize(query: str) -> list[Token]:
    """Tokenize a boolean search query string."""
    tokens: list[Token] = []
    i = 0
    while i < len(query):
        ch = query[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(":
            tokens.append(Token(kind=TokenKind.LPAREN, value="("))
            i += 1
        elif ch == ")":
            tokens.append(Token(kind=TokenKind.RPAREN, value=")"))
            i += 1
        elif ch == '"':
            # Quoted phrase
            end = query.find('"', i + 1)
            if end == -1:
                # Unterminated quote — treat rest as phrase
                phrase = query[i + 1 :]
                tokens.append(Token(kind=TokenKind.PHRASE, value=phrase))
                i = len(query)
            else:
                phrase = query[i + 1 : end]
                tokens.append(Token(kind=TokenKind.PHRASE, value=phrase))
                i = end + 1
        else:
            # Word token
            end = i
            while end < len(query) and not query[end].isspace() and query[end] not in '()"':
                end += 1
            word = query[i:end]
            if word.upper() == "AND":
                tokens.append(Token(kind=TokenKind.AND, value="AND"))
            elif word.upper() == "OR":
                tokens.append(Token(kind=TokenKind.OR, value="OR"))
            elif word.upper() == "NOT":
                tokens.append(Token(kind=TokenKind.NOT, value="NOT"))
            else:
                tokens.append(Token(kind=TokenKind.TERM, value=word))
            i = end
    tokens.append(Token(kind=TokenKind.EOF))
    return tokens


class _Parser:
    """Recursive descent parser for boolean search queries."""

    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def _peek(self) -> Token:
        return self._tokens[self._pos]

    def _advance(self) -> Token:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def _expect(self, kind: TokenKind) -> Token:
        tok = self._advance()
        if tok.kind != kind:
            raise ValueError(f"Expected {kind}, got {tok.kind} ({tok.value!r})")
        return tok

    def parse(self) -> QueryNode:
        node = self._or_expr()
        if self._peek().kind != TokenKind.EOF:
            raise ValueError(f"Unexpected token: {self._peek().value!r}")
        return node

    def _or_expr(self) -> QueryNode:
        left = self._and_expr()
        while self._peek().kind == TokenKind.OR:
            self._advance()
            right = self._and_expr()
            left = OrNode(left=left, right=right)
        return left

    def _and_expr(self) -> QueryNode:
        left = self._not_expr()
        while True:
            peek = self._peek()
            if peek.kind == TokenKind.AND:
                self._advance()
                right = self._not_expr()
                left = AndNode(left=left, right=right)
            elif peek.kind in (TokenKind.TERM, TokenKind.PHRASE, TokenKind.NOT, TokenKind.LPAREN):
                # Implicit AND
                right = self._not_expr()
                left = AndNode(left=left, right=right)
            else:
                break
        return left

    def _not_expr(self) -> QueryNode:
        if self._peek().kind == TokenKind.NOT:
            self._advance()
            child = self._primary()
            return NotNode(child=child)
        return self._primary()

    def _primary(self) -> QueryNode:
        tok = self._peek()
        if tok.kind == TokenKind.LPAREN:
            self._advance()
            node = self._or_expr()
            self._expect(TokenKind.RPAREN)
            return node
        if tok.kind == TokenKind.PHRASE:
            self._advance()
            return PhraseNode(phrase=tok.value)
        if tok.kind == TokenKind.TERM:
            self._advance()
            return TermNode(term=tok.value)
        raise ValueError(f"Unexpected token: {tok.kind} ({tok.value!r})")


def parse_query(query: str) -> QueryNode:
    """Parse a boolean search query string into an AST."""
    tokens = _tokenize(query)
    # Handle empty query
    if len(tokens) == 1 and tokens[0].kind == TokenKind.EOF:
        return TermNode(term="")
    return _Parser(tokens).parse()


def evaluate_query(node: QueryNode, text: str) -> bool:
    """Evaluate a parsed query against text (case-insensitive)."""
    text_lower = text.lower()
    return _eval(node, text_lower)


def _eval(node: QueryNode, text_lower: str) -> bool:
    if isinstance(node, TermNode):
        if not node.term:
            return True  # empty query matches everything
        return node.term.lower() in text_lower
    if isinstance(node, PhraseNode):
        if not node.phrase:
            return True
        return node.phrase.lower() in text_lower
    if isinstance(node, AndNode):
        return _eval(node.left, text_lower) and _eval(node.right, text_lower)
    if isinstance(node, OrNode):
        return _eval(node.left, text_lower) or _eval(node.right, text_lower)
    if isinstance(node, NotNode):
        return not _eval(node.child, text_lower)
    return False  # pragma: no cover
