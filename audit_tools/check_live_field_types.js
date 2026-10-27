// Read-only. Run in Navicat's MongoDB query window for match_agent.
// Results are flattened so Navicat does not show only "Array 1 Element".
db.products.aggregate([
  {$group: {
    _id: {
      material_code: {$type: "$material_code"},
      category: {$type: "$category"},
      standard_specs: {$type: "$standard_specs"},
      source_attributes: {$type: "$source_attributes"},
      quality_flags: {$type: "$quality_flags"}
    },
    records: {$sum: 1}
  }},
  {$project: {
    _id: 0,
    material_code_type: "$_id.material_code",
    category_type: "$_id.category",
    standard_specs_type: "$_id.standard_specs",
    source_attributes_type: "$_id.source_attributes",
    quality_flags_type: "$_id.quality_flags",
    records: 1
  }}
])
