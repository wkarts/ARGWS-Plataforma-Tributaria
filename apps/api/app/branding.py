"""Public visual identity of the customer-hosted installation.

The singleton is deliberately independent from the companies/CNPJs managed here.
Only an administrator of the organization that first configured this installation
may change its identity. No credentials or document data enter public responses.
"""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import AuditEvent, InstallationBrand, InstallationBrandAsset, User, now
from app.security import admin_only

router = APIRouter(tags=["Identidade da instalação"])

DEFAULT_NAME = "Gestão Fiscal"
DEFAULT_SHORT_NAME = "Fiscal"
DEFAULT_PRIMARY = "#255896"
DEFAULT_SECONDARY = "#102743"
MAX_LOGO_BYTES = 2 * 1024 * 1024
MAX_LOGO_PIXELS = 8_000_000
BASE = "/api/v1/installation/branding"


class BrandInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    version: int = Field(ge=0)
    display_name: str = Field(min_length=2, max_length=160)
    short_name: str = Field(min_length=2, max_length=40)
    primary_color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    secondary_color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    remove_logo: bool = False


def public_brand(brand: InstallationBrand | None) -> dict:
    if brand is None:
        return {
            "display_name": DEFAULT_NAME,
            "short_name": DEFAULT_SHORT_NAME,
            "primary_color": DEFAULT_PRIMARY,
            "secondary_color": DEFAULT_SECONDARY,
            "logo_url": None,
            "version": 0,
        }
    return {
        "display_name": brand.display_name,
        "short_name": brand.short_name,
        "primary_color": brand.primary_color if re.fullmatch(r"#[0-9a-fA-F]{6}", brand.primary_color) else DEFAULT_PRIMARY,
        "secondary_color": brand.secondary_color if re.fullmatch(r"#[0-9a-fA-F]{6}", brand.secondary_color) else DEFAULT_SECONDARY,
        "logo_url": f"{BASE}/logo.png?v={brand.version}" if brand.logo_sha256 else None,
        "version": brand.version,
    }


async def get_brand(session: AsyncSession) -> InstallationBrand | None:
    return await session.get(InstallationBrand, 1)


def normalize_logo(data: bytes) -> bytes:
    if not data or len(data) > MAX_LOGO_BYTES:
        raise HTTPException(422, "O logotipo deve ter até 2 MB")
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"} or source.width * source.height > MAX_LOGO_PIXELS:
                raise HTTPException(422, "Envie PNG, JPEG ou WebP com até 8 megapixels")
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="PNG", optimize=True)
        if output.tell() > MAX_LOGO_BYTES:
            raise HTTPException(422, "Reduza as dimensões do logotipo")
        return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(422, "Arquivo de logotipo inválido") from None


def primary_foreground(hex_color: str) -> str:
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [part / 12.92 if part <= .04045 else ((part + .055) / 1.055) ** 2.4
              for part in channels]
    luminance = .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]
    return "#102030" if luminance > .179 else "#FFFFFF"


@router.get(BASE)
async def read_brand(session: AsyncSession = Depends(get_session)) -> Response:
    payload = public_brand(await get_brand(session))
    return Response(json.dumps(payload, ensure_ascii=False), media_type="application/json",
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get(f"{BASE}/bootstrap.js", include_in_schema=False)
async def brand_bootstrap(session: AsyncSession = Depends(get_session)) -> Response:
    """Load only public identity before Vue's first paint; never expose secrets."""
    payload = json.dumps(public_brand(await get_brand(session)), ensure_ascii=True)
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    js = ("(()=>{const brand=window.__INSTALLATION_BRAND__=" + payload + ";"
          "document.title=brand.display_name;"
          "document.documentElement.style.setProperty('--brand-primary',brand.primary_color);"
          "document.documentElement.style.setProperty('--brand-secondary',brand.secondary_color);"
          "document.querySelector('meta[name=\"theme-color\"]')?.setAttribute('content',brand.primary_color);"
          "const v=Number.isSafeInteger(brand.version)&&brand.version>=0?brand.version:0;"
          "document.querySelector('link[rel=\"icon\"]')?.setAttribute('href','/api/v1/installation/branding/icon.png?size=32&v='+v);"
          "document.querySelector('link[rel=\"apple-touch-icon\"]')?.setAttribute('href','/api/v1/installation/branding/icon.png?size=180&v='+v);"
          "document.querySelector('link[rel=\"manifest\"]')?.setAttribute('href','/api/v1/installation/branding/manifest.webmanifest?v='+v);"
          "document.querySelector('link[data-installation-theme]')?.setAttribute('href','/api/v1/installation/branding/theme.css?v='+v);"
          "})();")
    return Response(js, media_type="application/javascript", headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Cross-Origin-Resource-Policy": "same-origin",
    })


@router.put(BASE)
async def save_brand(
    payload: str = Form(...),
    logo: UploadFile | None = File(default=None),
    user: User = Depends(admin_only),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        data = BrandInput.model_validate_json(payload)
    except ValidationError:
        raise HTTPException(422, "Revise nome, nome curto, cores e versão da identidade") from None
    if data.remove_logo and logo:
        raise HTTPException(422, "Não remova e envie um logotipo na mesma alteração")
    normalized = normalize_logo(await logo.read(MAX_LOGO_BYTES + 1)) if logo else None
    digest = sha256(normalized).hexdigest() if normalized else None
    brand = await session.scalar(select(InstallationBrand).where(InstallationBrand.id == 1).with_for_update())
    if brand and brand.owner_organization_id != user.organization_id:
        raise HTTPException(403, "A identidade pertence ao administrador desta instalação")
    if data.version != (brand.version if brand else 0):
        raise HTTPException(409, "A identidade foi alterada. Atualize a tela antes de salvar")
    if brand:
        logo_sha256 = digest or (None if data.remove_logo else brand.logo_sha256)
        result = await session.execute(update(InstallationBrand).where(
            InstallationBrand.id == 1, InstallationBrand.version == data.version,
        ).values(display_name=data.display_name, short_name=data.short_name,
                 primary_color=data.primary_color.upper(), secondary_color=data.secondary_color.upper(),
                 logo_sha256=logo_sha256, version=data.version + 1, updated_at=now()))
        if result.rowcount != 1:
            await session.rollback()
            raise HTTPException(409, "A identidade foi alterada. Atualize a tela antes de salvar")
    else:
        logo_sha256 = digest
        session.add(InstallationBrand(id=1, owner_organization_id=user.organization_id,
            display_name=data.display_name, short_name=data.short_name,
            primary_color=data.primary_color.upper(), secondary_color=data.secondary_color.upper(),
            logo_sha256=logo_sha256, version=1, updated_at=now()))
    if normalized and not await session.get(InstallationBrandAsset, digest):
        session.add(InstallationBrandAsset(sha256=digest, media_type="image/png", content=normalized))
    session.add(AuditEvent(organization_id=user.organization_id, actor=user.id,
        action="installation.brand.updated", entity_id="1", metadata_json={
            "version": data.version + 1, "display_name": data.display_name,
            "logo_configured": bool(logo_sha256),
        }))
    try:
        await session.flush()
        if logo_sha256:
            await session.execute(delete(InstallationBrandAsset).where(
                InstallationBrandAsset.sha256 != logo_sha256))
        else:
            await session.execute(delete(InstallationBrandAsset))
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "A identidade foi alterada. Atualize a tela antes de salvar") from None
    return public_brand(await get_brand(session))


@router.get(f"{BASE}/logo.png", include_in_schema=False)
async def brand_logo(session: AsyncSession = Depends(get_session)) -> Response:
    brand = await get_brand(session)
    if brand is None or brand.logo_sha256 is None:
        raise HTTPException(404, "Logotipo não configurado")
    asset = await session.get(InstallationBrandAsset, brand.logo_sha256)
    if asset is None:
        raise HTTPException(404, "Logotipo não encontrado")
    return Response(asset.content, media_type="image/png", headers={
        "Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff",
        "ETag": f'"{asset.sha256}"',
    })


@router.get(f"{BASE}/theme.css", include_in_schema=False)
async def brand_theme(session: AsyncSession = Depends(get_session)) -> Response:
    identity = public_brand(await get_brand(session))
    # Both colors are restricted to six hex digits by BrandInput.
    css = (f':root{{--brand-primary:{identity["primary_color"]};'
           f'--brand-secondary:{identity["secondary_color"]};'
           f'--brand-primary-on:{primary_foreground(identity["primary_color"])};}}')
    return Response(css, media_type="text/css", headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    })


@router.get(f"{BASE}/icon.png", include_in_schema=False)
async def brand_icon(size: int = Query(192), session: AsyncSession = Depends(get_session)) -> Response:
    if size not in {32, 180, 192, 512}:
        raise HTTPException(422, "Tamanho de ícone inválido")
    brand = await get_brand(session)
    identity = public_brand(brand)
    image = Image.new("RGBA", (size, size), identity["primary_color"])
    asset = await session.get(InstallationBrandAsset, brand.logo_sha256) if brand and brand.logo_sha256 else None
    if asset:
        with Image.open(BytesIO(asset.content)) as original:
            symbol = original.convert("RGBA")
            symbol.thumbnail((round(size * .8), round(size * .8)), Image.Resampling.LANCZOS)
            image.alpha_composite(symbol, ((size - symbol.width) // 2, (size - symbol.height) // 2))
    else:
        letters = "".join(word[0].upper() for word in identity["short_name"].split()[:2])
        if len(letters) < 2:
            letters = identity["short_name"][:2].upper()
        font = ImageFont.load_default(size=max(12, size // 3))
        ImageDraw.Draw(image).text((size // 2, size // 2), letters,
                                   fill="white", font=font, anchor="mm")
    output = BytesIO()
    image.save(output, format="PNG")
    return Response(output.getvalue(), media_type="image/png", headers={
        "Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff",
    })


@router.get(f"{BASE}/manifest.webmanifest", include_in_schema=False)
async def brand_manifest(session: AsyncSession = Depends(get_session)) -> Response:
    identity = public_brand(await get_brand(session))
    body = {"id": "/", "name": identity["display_name"], "short_name": identity["short_name"],
            "description": "Gestão e conferência de documentos fiscais", "lang": "pt-BR",
            "start_url": "/", "scope": "/", "display": "standalone",
            "background_color": "#F7F9FC", "theme_color": identity["primary_color"],
            "icons": [{"src": f'{BASE}/icon.png?size={size}&v={identity["version"]}',
                       "sizes": f"{size}x{size}", "type": "image/png", "purpose": "any"}
                      for size in (192, 512)]}
    return Response(json.dumps(body, ensure_ascii=False), media_type="application/manifest+json",
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
