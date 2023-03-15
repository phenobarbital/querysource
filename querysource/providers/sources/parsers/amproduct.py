"""Parser for Amazon Products
"""
from .xpath import xpathParser


product_model = {
    "name": ".//h1[@id='title']//span//text()",
    "url": ".//link[contains(@rel, 'canonical')]/@href",
    "brand": ".//tr[contains(@class, 'po-brand')]//td[contains(@class, 'a-span6')]//text()",
    "breadcrumb": ".//div[@id='wayfinding-breadcrumbs_feature_div']//ul//text()",
    "seller_url": ".//a[@id='bylineInfo']/@href",
    "price": ".//span[contains(@class, 'priceToPay')]//text()",
    "previous_price": ".//span[contains(@class, 'basisPrice')]//span[contains(@class, 'a-price')]//text()",
    "reviews": ".//span[@id='acrCustomerReviewText']//text()",
    "image": ".//img[@id='landingImage']/@src",
}


class amProduct(xpathParser):
    model: dict = product_model
