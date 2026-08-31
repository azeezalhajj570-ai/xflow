import subprocess
result = subprocess.run(
    ["odoo", "shell", "-d", "odoo", "--db_host", "db", "--db_password", "odoo18@2024!", "--http-port", "8097", "--no-xmlrpc"],
    input='''
domain = [("sale_ok", "=", True)]
products = env["product.product"].sudo().search(domain, limit=5)
print(f"Found: {len(products)} products")
for p in products:
    print(f"  - {p.name}: ${p.lst_price}")
''', capture_output=True, text=True, timeout=60
)
print(result.stdout)
print(result.stderr)
