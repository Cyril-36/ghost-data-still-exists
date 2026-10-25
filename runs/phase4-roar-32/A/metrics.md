# Evaluation: predictions.csv vs sample_output.csv

Measured by comparing predictions with labels for the same case set. This says nothing about cases without labels.

| Metric | Result |
|---|---|
| status_accuracy | 28/32 = 0.8750 |
| step_precision | 141/142 = 0.9930 |
| step_recall | 141/193 = 0.7306 |
| step_f1 | 282/335 = 0.8418 |
| field_accuracy_positional | 681/1158 = 0.5881 |
| sequence_accuracy | 1/32 = 0.0312 |
| exact_row_accuracy | 1/32 = 0.0312 |

Step F1 is shown as 2·TP / (predicted + expected steps).
Positional field accuracy counts each field of unmatched steps as an error.

## Status confusion

| Expected | Predicted | Count |
|---|---|---|
| COMPLETE | COMPLETE | 6 |
| COMPLETE | INCOMPLETE | 4 |
| INCOMPLETE | INCOMPLETE | 22 |

## Field errors

- evidence_id: 1
- operation: 36
- resource_id: 49
- state: 36
- system_id: 49
- unmatched_step: 306

## Mismatched cases (31)

- case_01: status COMPLETE -> INCOMPLETE; 1 missing, 0 extra; first divergent step 1
- case_02: status COMPLETE -> INCOMPLETE; 1 missing, 0 extra; first divergent step 1
- case_03: status COMPLETE -> COMPLETE; 2 missing, 0 extra; first divergent step 1
- case_04: status COMPLETE -> INCOMPLETE; 2 missing, 0 extra; first divergent step 1
- case_06: status INCOMPLETE -> INCOMPLETE; 1 missing, 0 extra; first divergent step 3
- case_07: status INCOMPLETE -> INCOMPLETE; 1 missing, 0 extra; first divergent step 3
- case_08: status INCOMPLETE -> INCOMPLETE; 1 missing, 0 extra; first divergent step 3
- case_09: status INCOMPLETE -> INCOMPLETE; 2 missing, 0 extra; first divergent step 3
- case_10: status INCOMPLETE -> INCOMPLETE; 1 missing, 0 extra; first divergent step 3
- case_11: status INCOMPLETE -> INCOMPLETE; 1 missing, 0 extra; first divergent step 11
- case_12: status INCOMPLETE -> INCOMPLETE; 2 missing, 0 extra; first divergent step 3
- case_13: status INCOMPLETE -> INCOMPLETE; 1 missing, 0 extra; first divergent step 8
- case_14: status INCOMPLETE -> INCOMPLETE; 1 missing, 0 extra; first divergent step 4
- case_15: status INCOMPLETE -> INCOMPLETE; 2 missing, 0 extra; first divergent step 3
- case_16: status INCOMPLETE -> INCOMPLETE; 1 missing, 0 extra; first divergent step 7
- case_17: status INCOMPLETE -> INCOMPLETE; 2 missing, 0 extra; first divergent step 3
- case_18: status INCOMPLETE -> INCOMPLETE; 2 missing, 0 extra; first divergent step 9
- case_19: status INCOMPLETE -> INCOMPLETE; 2 missing, 0 extra; first divergent step 5
- case_20: status INCOMPLETE -> INCOMPLETE; 2 missing, 0 extra; first divergent step 5
- case_21: status COMPLETE -> COMPLETE; 2 missing, 0 extra; first divergent step 1
- case_22: status INCOMPLETE -> INCOMPLETE; 2 missing, 0 extra; first divergent step 5
- case_23: status INCOMPLETE -> INCOMPLETE; 2 missing, 0 extra; first divergent step 5
- case_24: status INCOMPLETE -> INCOMPLETE; 1 missing, 0 extra; first divergent step 7
- case_25: status COMPLETE -> COMPLETE; 2 missing, 0 extra; first divergent step 1
- case_26: status COMPLETE -> COMPLETE; 2 missing, 0 extra; first divergent step 1
- case_27: status COMPLETE -> COMPLETE; 2 missing, 0 extra; first divergent step 1
- case_28: status COMPLETE -> COMPLETE; 2 missing, 0 extra; first divergent step 1
- case_29: status INCOMPLETE -> INCOMPLETE; 2 missing, 0 extra; first divergent step 9
- case_30: status INCOMPLETE -> INCOMPLETE; 2 missing, 0 extra; first divergent step 9
- case_31: status COMPLETE -> INCOMPLETE; 3 missing, 1 extra; first divergent step 1
- case_32: status INCOMPLETE -> INCOMPLETE; 2 missing, 0 extra; first divergent step 3
