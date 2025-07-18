import logging
from ninja import NinjaAPI, ModelSchema, Schema
from typing import List, Optional
from .models import ProductURL, ProductDetails
from ninja.errors import ValidationError
from django.db import IntegrityError
from django.http import HttpResponse
from django.core.exceptions import ObjectDoesNotExist
from decimal import Decimal
from datetime import datetime
from django.utils import timezone

# Configura o logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

api = NinjaAPI()

class ProductsDetailsIn(ModelSchema):
    class Config:
        model = ProductDetails
        model_fields = [
            'ean', 'sku', 'key_sku','loja', 'preco_final', 'data_hora', 'marketplace',
            'key_loja', 'descricao', 'review', 'imagem', 'status',
            'preco_pricing','url'
        ]

class ProductDetailsOut(Schema):
    ean: str
    sku: str
    loja: str
    preco_final: Decimal
    data_hora: datetime
    marketplace: str
    change_price: int
    key_loja: str
    descricao: str
    review: float
    imagem: str
    status: str
    preco_pricing: Optional[Decimal]
    url: str

class ProductURLInputSchema(Schema):
    ean_key: str
    ean: str
    brand: str
    url: str
    client_name: str

    @staticmethod
    def validate_ean(ean: str) -> str:
        if not (ean.isdigit() and len(ean) == 13):
            raise ValidationError("EAN must be a 13-digit number")
        return ean

    @staticmethod
    def validate_url(url: str) -> str:
        if not url.startswith(('http://', 'https://')):
            raise ValidationError("URL must start with http:// or https://")
        return url

    @staticmethod
    def validate_brand(brand: str) -> str:
        if not brand or brand.strip() == "":
            raise ValidationError("Brand cannot be empty")
        return brand

class SchemaProductURL(ModelSchema):
    class Config:
        model = ProductURL
        model_fields = ['ean_key', 'ean', 'brand', 'url', 'client', 'created_at', 'client_name']

@api.post('urls', response=List[SchemaProductURL])
def post_urls(request, payload: List[ProductURLInputSchema]):
    logger.info(f"Recebendo {len(payload)} URLs para salvar")
    created_products = []
    for url_data in payload:
        try:
            product = ProductURL(
                ean_key=url_data.ean_key,
                ean=url_data.ean,
                brand=url_data.brand,
                url=url_data.url,
                client_name=url_data.client_name,
                client=None
            )
            created_products.append(product)
        except IntegrityError as e:
            logger.error(f"Erro ao salvar produto (possível duplicata): {e}")
            continue
    ProductURL.objects.bulk_create(created_products, ignore_conflicts=True)
    created_eans = [url_data.ean for url_data in payload]
    logger.info(f"Produtos salvos: {created_eans}")
    return ProductURL.objects.filter(ean__in=created_eans)

@api.get('urls/', response=List[SchemaProductURL])
def get_urls(request):
    return ProductURL.objects.all()

@api.get('products/', response=List[ProductDetailsOut])
def get_products(request):
    return ProductDetails.objects.all()

@api.post("/products", response=List[ProductDetailsOut])
def create_products(request, products: List[ProductsDetailsIn]):
    created_products = []
    ean = products[0].ean if products else None  # Assume que todos os produtos têm o mesmo EAN

    # Obter todos os sellers existentes para o EAN
    existing_sellers = ProductDetails.objects.filter(ean=ean) if ean else ProductDetails.objects.none()
    existing_keys = {s.key_sku for s in existing_sellers}
    received_keys = {p.key_sku for p in products}

    # Processar produtos recebidos
    for product in products:
        try:
            product_data = product.dict()
            # Armazenar url para logs e saída, mas não salvar no modelo
            url = product_data.pop("url", "-")
            # Converter preco_final e preco_pricing para Decimal
            product_data["preco_final"] = Decimal(product_data["preco_final"])
            if product_data["preco_pricing"]:
                product_data["preco_pricing"] = Decimal(product_data["preco_pricing"])
            else:
                product_data["preco_pricing"] = None
            # Garantir que data_hora seja um objeto datetime
            product_data["data_hora"] = timezone.now()

            # Registrar log informativo para cada produto
            logger.info(
                f"Processando produto:\n"
                f"- Seller: {product_data['loja']}\n"
                f"- Produto EAN: {product_data['ean']}\n"
                f"- Preço: R$ {product_data['preco_final']:.2f}\n"
                f"- Total de mudanças: {product_data.get('change_price', 0)}\n"
                f"- Marketplace: {product_data['marketplace']}\n"
                f"- URL: {url}\n"
            )

            # Verifica se o produto já existe pelo key_sku
            existing_product = ProductDetails.objects.filter(key_sku=product_data["key_sku"]).first()
            if existing_product:
                # Preserva o change_price atual
                product_data["change_price"] = existing_product.change_price
                # Verifica mudança de preço com arredondamento para duas casas decimais
                if round(existing_product.preco_final, 2) != round(product_data["preco_final"], 2):
                    product_data["change_price"] += 1
                    logger.info(
                        f"Mudança de preço detectada!\n"
                        f"- Seller: {product_data['loja']}\n"
                        f"- Produto EAN: {product_data['ean']}\n"
                        f"- Preço anterior: R$ {existing_product.preco_final:.2f}\n"
                        f"- Novo preço: R$ {product_data['preco_final']:.2f}\n"
                        f"- Total de mudanças: {product_data['change_price']}\n"
                        f"- Marketplace: {product_data['marketplace']}\n"
                        f"- URL: {url}\n"
                    )
            else:
                # Novo produto, inicializa change_price
                product_data["change_price"] = 0

            # Define status como ativo
            product_data["status"] = "ativo"

            # Cria ou atualiza o produto
            new_product, created = ProductDetails.objects.update_or_create(
                key_sku=product_data["key_sku"],
                defaults=product_data
            )
            created_products.append((new_product, url))
        except Exception as e:
            logger.error(f"Erro ao salvar produto {product_data['key_sku']}: {str(e)}")
            return 422, {"detail": f"Erro ao salvar produto: {str(e)}", "data": product_data}

    # Marcar sellers ausentes como inativo
    current_time = timezone.now()
    for existing in existing_sellers:
        if existing.key_sku not in received_keys:
            existing.status = "inativo"
            existing.data_hora = current_time
            existing.save()
            url = ProductURL.objects.filter(ean=existing.ean).first().url if ProductURL.objects.filter(ean=existing.ean).exists() else "-"
            created_products.append((existing, url))

    # Retornar os produtos no formato do schema ProductDetailsOut
    return [
        ProductDetailsOut(
            ean=p.ean,
            sku=p.sku,
            loja=p.loja,
            preco_final=p.preco_final,
            data_hora=p.data_hora,
            marketplace=p.marketplace,
            change_price=p.change_price,
            key_loja=p.key_loja,
            key_sku=p.key_sku,
            descricao=p.descricao,
            review=p.review,
            imagem=p.imagem,
            status=p.status,
            preco_pricing=p.preco_pricing,
            url=url
        ) for p, url in created_products
    ]


