// Table "utterances": combines all Parquet files under insights/curated/utterances/ (one file per call).
let
    Source = AzureStorage.DataLake(StorageDfsUrl & "insights/curated/utterances", [HierarchicalNavigation = false]),
    ParquetFiles = Table.SelectRows(Source, each [Extension] = ".parquet"),
    Parsed = Table.AddColumn(ParquetFiles, "Data", each Parquet.Document([Content])),
    Combined = Table.Combine(Parsed[Data])
in
    Combined
