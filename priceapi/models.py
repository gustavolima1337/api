from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class ProductURL(models.Model):
    ean_key = models.CharField(primary_key=True)
    ean = models.CharField(max_length=13)
    brand = models.CharField(max_length=255)
    url = models.URLField(max_length=255)
    client_name = models.CharField(max_length=100)
    client = models.ForeignKey(User, on_delete=models.CASCADE, related_name='products', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(null=True, blank=True)

    def __str__(self):
        return f"{self.ean} - {self.brand}"

class ProductDetails(models.Model):
    ean = models.CharField(max_length=13)
    sku = models.CharField(max_length=50)  # Aumentado para 50, igual a ProductDetails
    loja = models.CharField(max_length=75, default="-")
    preco_final = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    data_hora = models.DateTimeField(default=timezone.now)
    marketplace = models.CharField(max_length=50, default="Desconhecido")
    change_price = models.IntegerField(default=0)
    key_loja = models.CharField(max_length=100, default="sem_loja")
    key_sku = models.CharField(max_length=255, primary_key=True, unique=True)
    descricao = models.TextField()
    review = models.FloatField(default=0.0)
    imagem = models.URLField(max_length=500, default="https://via.placeholder.com/150")
    status = models.CharField(max_length=10, choices=[("ativo", "Ativo"), ("inativo", "Inativo")], default="ativo")
    preco_pricing = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, null=True, blank=True)
    url = models.URLField(max_length=255)
    marca = models.CharField(max_length=100,default="Sem marca")

    def __str__(self):
        return f"{self.ean.ean} - {self.descricao[:50]}"
