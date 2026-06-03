{
    "name": "Lesotho Prepack Batch",
    "summary": "Batch-oriented prepacking workflow for Bahmni dispensing.",
    "version": "16.0.1.0.0",
    "category": "Manufacturing",
    "author": "MOH Lesotho",
    "license": "LGPL-3",
    "depends": [
        "mail",
        "mrp",
        "stock",
        "elmis_integration",
        "lesotho_manufacturing",
        "lesotho_base",
        "elmis_integration",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "report/prepack_label_report.xml",
        "views/stock_quant_views.xml",
        "views/prepack_batch_views.xml",
        "views/mrp_production_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "lesotho_prepack_batch/static/src/components/prepack_dashboard/prepack_dashboard.js",
            "lesotho_prepack_batch/static/src/components/prepack_dashboard/prepack_dashboard.xml",
            "lesotho_prepack_batch/static/src/components/prepack_dashboard/prepack_dashboard.scss",
        ],
    },
    "installable": True,
    "application": False,
}
