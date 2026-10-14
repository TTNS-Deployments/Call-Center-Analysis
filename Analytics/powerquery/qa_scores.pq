// Table "qa_scores": combines all Parquet files under insights/curated/qa_scores/ (one file per call).
let
    Source = AzureStorage.DataLake(StorageDfsUrl & "insights/curated/qa_scores", [HierarchicalNavigation = false]),
    ParquetFiles = Table.SelectRows(Source, each [Extension] = ".parquet"),
    Parsed = Table.AddColumn(ParquetFiles, "Data", each Parquet.Document([Content])),
    Combined = Table.Combine(Parsed[Data])
in
    Combined
