import type { ReactNode } from "react";

export type TableColumn<Row> = {
  key: string;
  header: string;
  render: (row: Row, index: number) => ReactNode;
  numeric?: boolean;
};

export function DataTable<Row>({
  caption,
  columns,
  rows,
  rowKey,
}: {
  caption: string;
  columns: Array<TableColumn<Row>>;
  rows: Row[];
  rowKey: (row: Row, index: number) => string;
}) {
  if (rows.length === 0) {
    return <p className="empty-inline">표시할 항목이 없습니다.</p>;
  }
  return (
    <div className="table-scroll" tabIndex={0} role="region" aria-label={caption}>
      <table>
        <caption>{caption}</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} scope="col" className={column.numeric ? "numeric" : undefined}>
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={rowKey(row, index)}>
              {columns.map((column) => (
                <td key={column.key} className={column.numeric ? "numeric" : undefined}>
                  {column.render(row, index)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
