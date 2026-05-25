{
    "name": "Lesotho Base",
    "summary": "MOH Dispensing Customizations",
    "version": "16.0.1.0.0",
    "category": "Localization",
    "author": "MOH Lesotho",
    "website": "",
    "license": "LGPL-3",
    "depends": [
        "base",
        "contacts",
        "sale",
        "account",
        "stock",
        "mrp",
    ],
    "data": [
        "views/create_prepack_wizard.xml",
        "security/ir.model.access.csv",
        "views/view_overrides.xml",
        "views/product_template_views.xml",
        "views/stock_location_views.xml", 
        "views/login_template.xml",
        "views/prepacking_placeholder_actions.xml",
        
    ],
    'assets': {
        'web.assets_backend': [
            'lesotho_base/static/src/css/custom_backend.css',
            'lesotho_base/static/src/js/logout_button.js',
            'lesotho_base/static/src/js/navbar_shortcuts.js',
            'lesotho_base/static/src/js/prepacking_placeholders.js',
            'lesotho_base/static/src/xml/navbar_shortcuts.xml',
            'lesotho_base/static/src/xml/prepacking_placeholders.xml',
            'lesotho_base/static/src/xml/welcome_screen.xml',
        ],
        'web.assets_frontend': [
            'lesotho_base/static/src/css/custom_frontend.css',
        ],
    },
    "installable": True,
    "application": False,
}
