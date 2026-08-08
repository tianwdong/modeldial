import Foundation

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    guard condition() else {
        fputs("FAIL: \(message)\n", stderr)
        exit(1)
    }
}

@main
private enum V4ScoreContractTestMain {
    static func main() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase

        let definition = try decoder.decode(
            BridgeQuestionDefinition.self,
            from: Data(
                #"{"id":"q1","question_number":1,"title":"Q1","capability_id":"reasoning","capability_label":"推理","detail_label":"细节","score_max":20}"#.utf8
            )
        )
        let result = try decoder.decode(
            BridgeQuestionResult.self,
            from: Data(
                #"{"question_id":"q1","question_title":"Q1","capability_id":"reasoning","capability_label":"推理","detail_label":"细节","phase":"scan","status":"pass","semantic_score":20,"semantic_total":20}"#.utf8
            )
        )
        let semantic = QuestionSemantic.from(definition)

        expect(semantic.scoreMax == 20, "question DTO should preserve the native score maximum")
        expect(result.semanticScoreText == "20/20", "result DTO should preserve the native score pair")

        let rows = ComparisonPresenter.questionRows(
            questions: [
                ComparisonPresenter.QuestionInput(
                    id: semantic.questionId,
                    shortLabel: semantic.shortLabel,
                    capabilityLabel: semantic.capabilityLabel
                )
            ],
            currentScores: [semantic.questionId: Double(result.semanticScore ?? 0)],
            candidateScores: [:],
            warningQuestionIDs: []
        )
        expect(rows.first?.currentScoreText == "20", "presenter should render the native score without rescaling")

        print("V4 score contract tests passed")
    }
}
