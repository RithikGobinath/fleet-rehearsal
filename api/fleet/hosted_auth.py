import jwt

from fleet.settings import HostedSettings


class HostedTokens:
    def __init__(self, settings: HostedSettings):
        self.settings = settings
        self.keys = jwt.PyJWKClient(settings.issuer + ".well-known/jwks.json", timeout=3)

    def subject(self, token: str) -> str | None:
        if len(token) > 8192 or token.count(".") != 2:
            return None
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if header.get("alg") != "RS256" or not isinstance(kid, str) or not 1 <= len(kid) <= 128:
                return None
            key = self.keys.get_signing_key(kid).key
            claims = jwt.decode(token, key, algorithms=["RS256"], issuer=self.settings.issuer,
                                audience=self.settings.audience,
                                options={"require": ["exp", "iss", "aud", "sub", "azp"]})
            if claims["azp"] != self.settings.client_id:
                return None
            subject = claims["sub"]
            if isinstance(subject, str) and 1 <= len(subject) <= 255 and "\x00" not in subject:
                return subject
        except jwt.PyJWTError:
            return None
        return None
