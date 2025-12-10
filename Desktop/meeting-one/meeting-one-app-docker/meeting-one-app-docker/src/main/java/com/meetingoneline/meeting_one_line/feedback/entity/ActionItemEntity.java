package com.meetingoneline.meeting_one_line.feedback.entity;

import com.meetingoneline.meeting_one_line.global.entity.BaseEntity;
import jakarta.persistence.*;
import lombok.*;

@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@Entity
@Table(name = "action_items")
public class ActionItemEntity extends BaseEntity {

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "feedback_id", nullable = false)
    private FeedbackEntity feedback;

    @Column(nullable = false, length = 100)
    private String name; // 담당자

    @Column(nullable = false, columnDefinition = "TEXT")
    private String content; // 내용

    @Column(name = "order_index")
    private Integer orderIndex;

    public static ActionItemEntity create(FeedbackEntity feedback, String name, String content, int orderIndex) {
        ActionItemEntity item = new ActionItemEntity();
        item.feedback = feedback;
        item.name = name;
        item.content = content;
        item.orderIndex = orderIndex;
        return item;
    }
}
