import { useState } from "react";

export default function QueryInterface() {
  const [query, setQuery] = useState("");
  const [subject, setSubject] = useState("");
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!query.trim() || !subject.trim()) return;

    setLoading(true);
    try {
      const formData = new FormData();
      formData.append("query", query);
      formData.append("subject", subject);

      const res = await fetch("http://localhost:8000/query_folder", {
        method: "POST",
        body: formData,
      });

      if (res.ok) {
        const data = await res.json();
        setResult(data);
      } else {
        const error = await res.json();
        setResult({
          query: query,
          answer: `Error: ${error.detail || 'Query failed'}`,
          source_documents: []
        });
      }
    } catch (error) {
      console.error("Query error:", error);
      setResult({
        query: query,
        answer: "Query failed. Please try again.",
        source_documents: []
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="query-interface">
      <h3>Query Your Notes</h3>
      
      <form onSubmit={handleSubmit} className="query-form">
        <div className="query-form-group">
          <label className="query-form-label">
            Subject Folder:
          </label>
          <input
            type="text"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            placeholder="e.g., Science, Maths, SST"
            className="query-form-input"
          />
        </div>
        
        <div className="query-form-group">
          <label className="query-form-label">
            Your Question:
          </label>
          <textarea
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Ask a question about your notes..."
            rows={3}
            className="query-form-textarea"
          />
        </div>
        
        <button
          type="submit"
          disabled={loading || !query.trim() || !subject.trim()}
          className="query-submit-btn"
        >
          {loading ? "🔄 Processing..." : "🚀 Ask Question"}
        </button>
      </form>

      {result && (
        <div className="query-result">
          <h4>Answer:</h4>
          <div className="query-answer">
            {result.answer}
          </div>
          
          {result.source_documents && result.source_documents.length > 0 && (
            <div className="query-sources">
              <h5>Sources:</h5>
              <div>
                {result.source_documents.map((doc, index) => (
                  <div key={index} className="query-source-item">
                    {doc.content.substring(0, 200)}...
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}