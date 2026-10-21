export default function Loading() {
  return (
    <div role="status" aria-busy="true" aria-label="Loading" className="space-y-4">
      <div className="skeleton h-7 w-56" />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        {Array.from({ length: 8 }, (_, i) => <div key={i} className="skeleton h-20" />)}
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="skeleton h-72 lg:col-span-2" />
        <div className="skeleton h-72" />
      </div>
    </div>
  );
}
