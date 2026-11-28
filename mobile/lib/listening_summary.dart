import 'dart:async';
import 'package:flutter/material.dart';
import 'api.dart';

String listeningTime(int ms){
  final minutes=ms~/60000;
  if(minutes>=60)return '${minutes~/60} ч ${minutes%60} мин';
  if(minutes>0)return '$minutes мин';
  return '${ms~/1000} сек';
}

class ListeningSummaryPage extends StatefulWidget {
  final LainApi api;
  const ListeningSummaryPage({super.key,required this.api});
  @override State<ListeningSummaryPage> createState()=>_ListeningSummaryPageState();
}
class _ListeningSummaryPageState extends State<ListeningSummaryPage>{
  String period='30';
  Map<String,dynamic>? data;
  bool busy=false;
  String? error;
  int _revision=0;
  @override void initState(){super.initState();unawaited(load());}
  Future<void> load()async{
    final revision=++_revision;
    setState((){busy=true;error=null;data=null;});
    try{
      final result=Map<String,dynamic>.from(await widget.api.request('GET','/v1/listening-summary',query:{'period':period}) as Map);
      if(mounted&&revision==_revision)setState(()=>data=result);
    }catch(e){if(mounted&&revision==_revision)setState(()=>error=e.toString());}
    finally{if(mounted&&revision==_revision)setState(()=>busy=false);}
  }
  Widget metric(String title,String value)=>Card(child:Padding(padding:const EdgeInsets.all(14),child:Column(
    crossAxisAlignment:CrossAxisAlignment.start,children:[
      Text(value,style:Theme.of(context).textTheme.headlineSmall),
      const SizedBox(height:4),Text(title),
    ])));
  Widget ranking(String title,List<dynamic> rows,{required bool artists}){
    final max=rows.isEmpty?1:(rows.first['listened_ms'] as int).clamp(1,1<<53);
    return Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
      Text(title,style:Theme.of(context).textTheme.titleLarge),
      if(rows.isEmpty)const Text('Пока нет данных'),
      for(var i=0;i<rows.length;i++)
        Padding(padding:const EdgeInsets.symmetric(vertical:8),child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
          Row(children:[
            Expanded(child:Text('${i+1}. ${artists?rows[i]['artist']:rows[i]['title']}',maxLines:1,overflow:TextOverflow.ellipsis)),
            Text(listeningTime(rows[i]['listened_ms'] as int)),
          ]),
          if(!artists)Text(rows[i]['artist'] as String,style:Theme.of(context).textTheme.bodySmall),
          const SizedBox(height:5),
          LinearProgressIndicator(value:((rows[i]['listened_ms'] as int)/max).clamp(0,1).toDouble()),
        ])),
    ]);
  }
  Widget days(List<dynamic> rows){
    if(rows.isEmpty)return const Text('Пока нет данных');
    final recent=rows.length>30?rows.sublist(rows.length-30):rows;
    final max=recent.fold<int>(1,(n,r)=>(r['listened_ms'] as int)>n?r['listened_ms'] as int:n);
    return Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
      const Text('Последние 30 дней с зарегистрированными событиями'),
      for(final row in recent.reversed)
        Padding(padding:const EdgeInsets.symmetric(vertical:4),child:Row(children:[
          SizedBox(width:90,child:Text(row['day'] as String)),
          Expanded(child:LinearProgressIndicator(value:((row['listened_ms'] as int)/max).clamp(0,1).toDouble())),
          const SizedBox(width:10),Text(listeningTime(row['listened_ms'] as int)),
        ])),
    ]);
  }
  @override Widget build(BuildContext context){
    final values=data;
    return Scaffold(appBar:AppBar(title:const Text('Статистика прослушивания'),actions:[
      IconButton(onPressed:busy?null:load,icon:const Icon(Icons.refresh)),
    ]),body:ListView(padding:const EdgeInsets.all(16),children:[
      SegmentedButton<String>(segments:const [
        ButtonSegment(value:'7',label:Text('7 дн.')),
        ButtonSegment(value:'30',label:Text('30 дн.')),
        ButtonSegment(value:'365',label:Text('Год')),
        ButtonSegment(value:'all',label:Text('Всё')),
      ],selected:{period},onSelectionChanged:(v){setState(()=>period=v.single);unawaited(load());}),
      const SizedBox(height:12),
      if(busy)const LinearProgressIndicator(),
      if(error!=null)Text(error!,style:TextStyle(color:Theme.of(context).colorScheme.error)),
      if(values!=null)...[
        metric('Накопленное время',listeningTime(values['listened_ms'] as int)),
        Row(children:[
          Expanded(child:metric('Сеансы','${values['sessions']}')),
          Expanded(child:metric('Треки','${values['unique_tracks']}')),
        ]),
        Row(children:[
          Expanded(child:metric('Исполнители','${values['unique_artists']}')),
          Expanded(child:metric('Дослушано ≥80%','${values['substantial_completions']}')),
        ]),
        Text('Ранние пропуски: ${values['early_skips']}'),
        const SizedBox(height:20),
        ranking('Любимые исполнители по времени',values['top_artists'] as List,artists:true),
        const SizedBox(height:20),
        ranking('Топ треков по времени',values['top_tracks'] as List,artists:false),
        const SizedBox(height:20),
        Text('По дням',style:Theme.of(context).textTheme.titleLarge),
        days(values['daily'] as List),
        const SizedBox(height:12),
        const Text('Только события плеера LAIN. Время является оценкой клиента; отправки аудио в Telegram не учитываются. Даты — день получения первого отчёта сервером (UTC), поэтому поздняя офлайн-синхронизация может сместить день. Активные или аварийно завершённые сеансы могут быть неполными.'),
      ],
    ]));
  }
}
