// Table "compliance": combines all Parquet files under insights/curated/compliance/ (one file per call).
let
    Source = AzureStorage.DataLake(StorageDfsUrl & "insights/curated/compliance", [HierarchicalNavigation = false]),
    ParquetFiles = Table.SelectRows(Source, each [Extension] = ".parquet"),
    Parsed = Table.AddColumn(ParquetFiles, "Data", each Parquet.Document([Content])),
    Combined = Table.Combine(Parsed[Data])
in
    Combined
